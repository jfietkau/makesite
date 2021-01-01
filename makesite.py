#!/usr/bin/env python3

# The MIT License (MIT)
#
# Copyright (c) 2020 Julian Fietkau, 2018 Sunaina Pai
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
# CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.


import base64
import collections
import copy
import datetime
import distutils.dir_util
import glob
import hashlib
import htmlmin
import jinja2
import json
import os
import PIL.Image
import PIL.ImageChops
import re
import rcssmin
import shutil
import stat
import subprocess
import sys

import orcid


def fread(filename):
    """Read file and close the file."""
    with open(filename, 'r') as f:
        return f.read()


def fwrite(filename, text):
    """Write content to file and close the file."""
    basedir = os.path.dirname(filename)
    if not os.path.isdir(basedir):
        os.makedirs(basedir)

    with open(filename, 'w') as f:
        f.write(text)


def log(msg, *args):
    """Log message with specified arguments."""
    sys.stderr.write(msg.format(*args) + '\n')


def read_headers(text):
    """Parse headers in text and yield (key, value, end-index) tuples."""
    for match in re.finditer(r'\s*<!--\s*(.+?)\s*:\s+(.+?)\s*-->\s*|.+', text):
        if not match.group(1):
            break
        yield match.group(1), match.group(2), match.end()


def rfc_2822_format(date):
    if not isinstance(date, datetime.datetime):
        date = datetime.datetime.strptime(date, '%Y-%m-%d')
    return date.strftime('%a, %d %b %Y %H:%M:%S +0000')


def pretty_format(date):
    if not isinstance(date, datetime.datetime):
        date = datetime.datetime.strptime(date, '%Y-%m-%d')
    ordinal_suffix = 'th'
    if date.day in [1, 21, 31]:
        ordinal_suffix = 'st'
    elif date.day in [2, 22]:
        ordinal_suffix = 'nd'
    elif date.day in [3, 23]:
        ordinal_suffix = 'rd'
    return date.strftime('%b %-d' + ordinal_suffix + ', %Y')


def read_content(filename):
    """Read content and metadata from file into a dictionary."""
    # Read file content.
    text = fread(filename)

    # Read metadata and save it in a dictionary.
    date_slug = os.path.basename(filename).split('.')[0]
    match = re.search(r'^(?:(\d\d\d\d-\d\d-\d\d)-)?(.+)$', date_slug)
    content = {
        'date': match.group(1) or '1970-01-01',
        'slug': match.group(2),
    }

    # Read headers.
    end = 0
    for key, val, end in read_headers(text):
        content[key] = val

    # Separate content from headers.
    text = text[end:]

    # Convert Markdown content to HTML.
    if filename.endswith(('.md', '.mkd', '.mkdn', '.mdown', '.markdown')):
        try:
            if _test == 'ImportError':
                raise ImportError('Error forced by test')
            import commonmark
            text = commonmark.commonmark(text)
        except ImportError as e:
            log('WARNING: Cannot render Markdown in {}: {}', filename, str(e))

    # Update the dictionary with content and RFC 2822 date.
    content.update({
        'content': text,
        'rfc_2822_date': rfc_2822_format(content['date'])
    })

    return content


def render(template, **params):
    """Replace placeholders in template with values from params."""
    return re.sub(r'{{\s*([^}\s]+)\s*}}',
                  lambda match: str(params.get(match.group(1), match.group(0))),
                  template)


def optimize_for_build(source, target):
    if target.endswith('.js'):
        with open(source, 'r') as f:
            start = f.read(13)
            # Only minify modern JS files (since the site contains some less important legacy code)
            if start == '\'use strict\';':
                jar_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'closure-compiler-v*.jar')
                jar_path = glob.glob(jar_path)[0]
                subprocess.run(['java', '-jar', jar_path, '--compilation_level', 'ADVANCED', '--js', source,
                                '--js_output_file', target, '--language_in', 'ECMASCRIPT_2015', '--language_out', 'ECMASCRIPT_2015',
                                '--strict_mode_input', '--formatting', 'SINGLE_QUOTES'])
                shutil.copystat(source, target)
            else:
                shutil.copy2(source, target)
    elif target.endswith('.svg'):
        subprocess.run(['svgo', source, '-o', target])
        shutil.copystat(source, target)
    else:
        shutil.copy2(source, target)


def add_to_build(source, target, params):
    link_if_bigger_than = 4 * 1024 * 1024
    build_permissions = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH
    build_path = os.path.join(params['data_root'], 'build', params['build_target'])
    if target.startswith('/'):
        target = target[1:]
    target = os.path.join(params['site_dir'], target)
    if target.endswith('.html'):
        if os.path.isfile(source):
            source = fread(source)
        source = htmlmin.minify(source, remove_empty_space=True, remove_comments=True, remove_optional_attribute_quotes=False)
    if target.endswith('.css'):
        if os.path.isfile(source):
            source = fread(source)
        source = rcssmin.cssmin(source)
    if 'file_hash' not in params:
      params['file_hash'] = {}
    params['file_hash'][target] = base64.b64encode(hashlib.sha256(source.encode('utf-8')).digest(), altchars=b'-_').decode('ascii')[:16]
    target_path = os.path.join(build_path, target)
    if not os.path.isfile(target_path):
        target_dir = os.path.dirname(target_path)
        if not os.path.isdir(target_dir):
            os.makedirs(target_dir)
        # check if source is a path or direct file contents
        if not os.path.isfile(source):
            log('Adding {} from inline data ...'.format(target))
            fwrite(target_path, source)
        else:
            log('Adding {} from {} ...'.format(target, source))
            if os.path.getsize(source) > link_if_bigger_than:
                os.symlink(source, target_path)
            else:
                optimize_for_build(source, target_path)
        os.chmod(target_path, build_permissions)
    else:
        target_stat = os.stat(target_path)
        if not os.path.isfile(source):
            target_content = fread(target_path)
            if source != target_content:
                log('Adding {} from inline data ...'.format(target))
                fwrite(target_path, source)
                os.chmod(target_path, build_permissions)
            else:
                # log('Skipping {} - existing file is identical'.format(target))
                pass
        else:
            source_stat = os.stat(source)
            if source_stat.st_mtime != target_stat.st_mtime or (source_stat.st_size != target_stat.st_size and not (source.endswith('.js') or source.endswith('.svg'))):
                log('Adding {} from {} ...'.format(target, source))
                if os.path.getsize(source) > link_if_bigger_than:
                    os.remove(target_path)
                    os.symlink(source, target_path)
                    os.chmod(target_path, build_permissions)
                else:
                    optimize_for_build(source, target_path)
            else:
                # log('Skipping {} - existing file is identical'.format(target))
                pass


def sort_into_structure(title, breadcrumb, path, weight, structure):
    current = structure
    while '/' in breadcrumb:
        segment = breadcrumb[0:breadcrumb.index('/')]
        if segment not in current:
            current[segment] = {}
        if 'children' not in current[segment]:
            current[segment]['children'] = {}
        current = current[segment]['children']
        breadcrumb = breadcrumb[breadcrumb.index('/')+1:]
    if breadcrumb not in current:
        current[breadcrumb] = {}
    current[breadcrumb]['title'] = title
    current[breadcrumb]['path'] = path
    current[breadcrumb]['weight'] = weight
    return structure


def cleanup_structure(structure, collate_common=False):
    shared_keys = None
    for entry in structure:
        if 'children' in structure[entry]:
            sections = [section for section in structure if 'children' in structure[section]]
            if collate_common and len(sections) > 1:
                if shared_keys is None:
                    shared_keys = [key for key in structure[entry]['children']]
                else:
                    for key in copy.copy(shared_keys):
                        if key not in structure[entry]['children']:
                            shared_keys.remove(key)
    if shared_keys is not None and len(shared_keys) > 0:
        shared_entries = []
        for shared_key in shared_keys:
            entries = []
            for section in structure:
                if shared_key in structure[section]['children']:
                    entries.append(structure[section]['children'][shared_key])
                    del structure[section]['children'][shared_key]
            shared_entries.append(entries[0])
        for shared_entry in shared_entries:
            structure[shared_entry['title']] = shared_entry
    for entry in structure:
        if structure[entry]['title'].startswith('Student Project: '):
            structure[entry]['title'] = structure[entry]['title'][17:]
        if 'children' in structure[entry]:
            cleanup_structure(structure[entry]['children'], collate_common=False)
            children = [structure[entry]['children'][child] for child in structure[entry]['children']]
            children.sort(key=lambda c: c['weight'])
            structure[entry]['children'] = children


def make_pages(page_list, destination, template, **params):
    """Generate pages from page content."""
    items = []

    for src_path in page_list:
        if os.path.basename(src_path)[0].isdigit():
            continue

        content = read_content(src_path)
        for prop in list(content.keys()):
            if prop.startswith('og:'):
                name = prop[3:]
                value = content[prop]
                if 'open_graph' not in content:
                    content['open_graph'] = {}
                content['open_graph'][name] = value

        page_params = dict(params, **content)

        items.append(content)

        dst_path = render(destination, **page_params)
        
        page_params['self_path'] = '/' + dst_path
        if page_params['self_path'].endswith('.html'):
            page_params['self_path'] = page_params['self_path'][:-5]
        if page_params['self_path'].endswith('index'):
            page_params['self_path'] = page_params['self_path'][:-5]
        if page_params['self_path'].endswith('/'):
            page_params['self_path'] = page_params['self_path'][:-1]
        if os.path.basename(src_path)[0] == '_':
            del page_params['self_path']

        extra_head = []
        if dst_path == 'imprint.html':
            extra_head = ['<meta name="robots" content="noindex, follow">']
        
        output = template.render(extra_head=extra_head, **page_params)

        if os.path.basename(src_path)[0] != '_' and os.path.basename(src_path) != 'index.html':
            if 'breadcrumb' in page_params:
                if ' ' in page_params['breadcrumb']:
                    breadcrumb, weight = page_params['breadcrumb'].split(' ')
                    weight = int(weight)
                else:
                    breadcrumb = page_params['breadcrumb']
                    weight = 0
            else:
                breadcrumb = dst_path[:-5]
                weight = 0
            sort_into_structure(page_params['title'], params['current_site'] + '/' + breadcrumb, dst_path[:-5], weight, params['structure'])
        add_to_build(output, dst_path, params)

    return sorted(items, key=lambda x: x['date'], reverse=True)


def prepare_pub_files(pubs, params, template_env):
    source_dir = os.path.join(params['data_root'], 'content', 'science')
    cache_dir = os.path.join(params['data_root'], 'cache', 'publications')
    if not os.path.isdir(cache_dir):
        os.makedirs(cache_dir)
    bibtex_type_map = {
        'conference-paper': 'inproceedings',
        'conference-poster': 'inproceedings'
    }
    for pub in pubs:
        if 'url_id' not in pub:
            continue
        pub_files = glob.glob(os.path.join(source_dir, str(pub['id'])+'.*'))
        pub_files.sort()
        for pub_file in pub_files:
            extension = os.path.splitext(pub_file)[1]
            if extension == '.html' and ('not_published_yet' not in pub or params['build_target'] == 'dev'):
               pub['content_html'] = fread(pub_file)
               continue
            add_to_build(pub_file, pub['url_id'] + extension, params)
            if 'not_published_yet' not in pub:
                pub['has_download_'+extension[1:]] = True
            if extension == '.pdf':
                thumbnail_base_size = 400
                for size_factor in [1, 2, 3]:
                    thumbnail_filename = pub['url_id'] + '_thumbnail.'
                    if size_factor != 1:
                        thumbnail_filename = thumbnail_filename[:-1] + '-' + str(size_factor) + 'x.'
                    thumbnail_path = os.path.join(cache_dir, thumbnail_filename)
                    if not os.path.isfile(thumbnail_path + 'png'):
                        thumbnail_interim = thumbnail_path[:-1] + '-precrush.png'
                        subprocess.run(['convert', '-density', '600', pub_file+'[0]',
                                        '-alpha', 'remove', '-resize', str(thumbnail_base_size * size_factor), thumbnail_interim])
                        image = PIL.Image.open(thumbnail_interim)
                        image = image.convert('RGB')
                        image_grayscale = image.convert('L').convert('RGB')
                        difference = PIL.ImageChops.difference(image, image_grayscale)
                        tint_sum = 0
                        for pixel in difference.getdata():
                            if pixel != (0, 0, 0):
                                tint_sum += pixel[0] + pixel[1] + pixel[2]
                        tinted_quotient = tint_sum / (image.width * image.height)
                        if tinted_quotient < 0.1:
                            image = image.convert('L')
                        image.save(thumbnail_interim)
                        subprocess.run(['pngcrush', thumbnail_interim, thumbnail_path + 'png'])
                        os.remove(thumbnail_interim)
                        pub['thumbnail_size'] = list(image.size)
                    add_to_build(thumbnail_path + 'png', os.path.join('assets', thumbnail_filename + 'png'), params)
                    if not os.path.isfile(thumbnail_path + 'webp'):
                        subprocess.run(['cwebp', '-preset', 'text', '-q', '35', '-m', '6', '-noalpha', thumbnail_path + 'png', '-o', thumbnail_path + 'webp'])
                    add_to_build(thumbnail_path + 'webp', os.path.join('assets', thumbnail_filename + 'webp'), params)
                    if not os.path.isfile(thumbnail_path + 'avif'):
                        subprocess.run(['cavif', '--quality', '35', thumbnail_path + 'png', '-o', thumbnail_path + 'avif'])
                    add_to_build(thumbnail_path + 'avif', os.path.join('assets', thumbnail_filename + 'avif'), params)
                if 'thumbnail_size' not in pub:
                    image = PIL.Image.open(thumbnail_path + 'png')
                    pub['thumbnail_size'] = list(image.size)
                pub['has_thumbnail'] = True
                if 'content_html' not in pub and 'not_published_yet' not in pub:
                    if not os.path.isfile(os.path.join(cache_dir, pub['url_id'] + '_page1.svg')):
                        svg_path = os.path.join(cache_dir, pub['url_id'] + '_page%d.svg')
                        subprocess.run(['pdf2svg', pub_file, svg_path, 'all'])
                    svg_pages = glob.glob(os.path.join(cache_dir, pub['url_id'] + '_page*.svg'))
                    for svg in svg_pages:
                        add_to_build(svg, os.path.join('assets', os.path.basename(svg)), params)
                    if len(svg_pages) > 0:
                        pub['content_svg'] = len(svg_pages)

        bibtex_data = collections.OrderedDict()
        if 'authors' in pub:
            bibtex_data['author'] = ' AND '.join(pub['authors'])
            bibtex_id = pub['authors'][0].split(', ')[0]
            if len(pub['authors']) > 1:
                bibtex_id += ''.join(name[0] for name in pub['authors'][1:])
        else:
            bibtex_id = 'Anonymous'
        bibtex_data['title'] = pub['title']
        bibtex_data['year'] = pub['year']
        bibtex_id += pub['year']
        bibtex_id = bibtex_id.replace('ä', 'ae').replace('ö', 'oe').replace('ü', 'ue')
        bibtex_id = bibtex_id.replace('Ä', 'Ae').replace('Ö', 'Oe').replace('Ü', 'Ue')
        bibtex_id = bibtex_id.replace('ß', 'ss')
        bibtex_type = None
        if pub['type'] in bibtex_type_map:
            bibtex_type = bibtex_type_map[pub['type']]
        if pub['type'] == 'dissertation-thesis':
            if pub['thesis-type'] == 'phd':
                bibtex_type = 'phdthesis'
                bibtex_data['school'] = pub['thesis-university']
            elif pub['thesis-type'] == 'msc':
                bibtex_type = 'mastersthesis'
                bibtex_data['school'] = pub['thesis-university']
            elif pub['thesis-type'] == 'bsc':
                bibtex_type = 'misc'
                bibtex_data['howpublished'] = 'Bachelor thesis, ' + pub['thesis-university']
        if 'journal' in pub:
            bibtex_data['booktitle'] = pub['journal']
        if 'editors' in pub:
            bibtex_data['editor'] = ' AND '.join(pub['editors'])
        if 'publisher' in pub:
            bibtex_data['publisher'] = pub['publisher']
        if 'address' in pub:
            bibtex_data['address'] = pub['address']
        if 'series' in pub:
            bibtex_data['series'] = pub['series']
        if 'volume' in pub:
            bibtex_data['volume'] = pub['volume']
        if 'pages' in pub:
            bibtex_data['pages'] = pub['pages'].replace('-', '--')
        if 'numpages' in pub:
            bibtex_data['numpages'] = pub['numpages']
        if 'location' in pub:
            bibtex_data['location'] = pub['location']
        if 'doi' in pub:
            bibtex_data['doi'] = pub['doi']
        if 'isbn' in pub:
            bibtex_data['isbn'] = pub['isbn']
        elif 'parent-isbn' in pub:
            bibtex_data['isbn'] = pub['parent-isbn']
        if 'keywords' in pub:
            bibtex_data['keywords'] = ', '.join(pub['keywords'])
        if 'canonical_url' in pub:
            bibtex_data['url'] = pub['canonical_url']
        else:
            bibtex_data['url'] = params['protocol'] + params['hostname'] + params['hostname_suffix'] + '/' + pub['url_id']
        if bibtex_type is None:
            print('No type mapping found:', pub['type'])
        bibtex = '@' + bibtex_type + '{' + bibtex_id + ',\n'
        for part in bibtex_data:
            value = bibtex_data[part]
            value = value.replace('ä', '{\\"{a}}').replace('ö', '{\\"{o}}').replace('ü', '{\\"{u}}')
            value = value.replace('Ä', '{\\"{A}}').replace('Ö', '{\\"{O}}').replace('Ü', '{\\"{U}}')
            value = value.replace('ß', '{\\ss}')
            value = value.replace('–', '--')
            bibtex += '  ' + part + ' = {' + value + '},\n'
        bibtex = bibtex[:-2] + '\n}'
        add_to_build(bibtex, pub['url_id']+'.bib', params)
        pub['has_cite_bibtex'] = True

        pub_template = template_env.get_template('science/publication-page.html')
        params['title'] = pub['title']
        params['self_path'] = '/' + pub['url_id']
        open_graph = {
            'type': 'article',
            'image': params['protocol'] + params['hostname'] + params['hostname_suffix'] + '/assets/' + pub['url_id'] + '_thumbnail-2x.png',
            'image:alt': 'First page of the print version of this article'
        }
        if 'abstract' in pub:
            sentences = pub['abstract'].split('. ')
            description = ''
            for sentence in sentences:
                description += sentence + '. '
                if len(description) > 150:
                    description = description[:-1]
                    break
            open_graph['description'] = description
        output = pub_template.render(publication=pub, open_graph=open_graph, css='publication.css', **params)
        weight = -1 * int(pub['year']+pub['month']+pub['day'])
        sort_into_structure(pub['title'], params['current_site'] + '/publications/' + pub['url_id'], pub['url_id'], weight, params['structure'])
        add_to_build(output, pub['url_id']+'.html', params)


def compile_site(site, params):

    for static_source in ['all', site['name'].lower()]:
        static_path = os.path.join(params['data_root'], 'static', static_source)
        if os.path.isdir(static_path):
            everything = glob.glob(os.path.join(static_path, '**'), recursive=True)
            for item in everything:
                if os.path.isdir(item):
                    continue
                target_path = item[len(static_path):]
                add_to_build(item, target_path, params)

    templates_path = os.path.join(params['data_root'], 'templates')
    template_env = jinja2.Environment(loader=jinja2.FileSystemLoader(templates_path))

    content_path = os.path.join(params['data_root'], 'content', 'all')
    site_content_path = os.path.join(params['data_root'], 'content', site['name'].lower())

    additional_templates = ['main.css', 'robots.txt']
    for additional_template in additional_templates:
        template = template_env.get_template(additional_template)
        output = template.render(**params)
        add_to_build(output, additional_template, params)

    page_template = template_env.get_template('page.html')
    page_list = []
    for candidate in glob.glob(os.path.join(site_content_path, '*.html')):
        if candidate.endswith('.include.html'):
            continue
        page_list.append(candidate)
    for candidate in glob.glob(os.path.join(content_path, '*.html')):
        if candidate.endswith('.include.html'):
            continue
        if candidate.replace(content_path, site_content_path) in page_list:
            continue
        page_list.append(candidate)
    make_pages(page_list, '{{ slug }}.html', page_template, **params)

    if site['name'] == 'Science':
        orcid_cache_dir = os.path.join(params['data_root'], 'cache', 'orcid')
        if not os.path.isdir(orcid_cache_dir):
            os.makedirs(orcid_cache_dir)
        pubs = orcid.get(site['orcid'], orcid_cache_dir)
        pubs.sort(key=lambda p: p['year']+p['month']+p['day'])
        pubs.reverse()
        with open(os.path.join(params['data_root'], 'content', 'science', 'publications.json')) as fp:
            metadata = json.load(fp)
        for pub in pubs:
            pub_id = str(pub['id'])
            if pub_id in metadata:
                pub.update(metadata[pub_id])
            else:
                print('No additional metadata four publication with ID:', pub_id)
            pub['rfc_2822_date'] = rfc_2822_format(datetime.datetime(int(pub['year']), int(pub['month']), int(pub['day']), 0, 0, 0))
            publish_date = datetime.datetime.strptime(pub['year'] + '-' + pub['month'] + '-' + pub['day'], '%Y-%m-%d')
            if publish_date > datetime.datetime.utcnow():
                pub['not_published_yet'] = True
        prepare_pub_files(pubs, params, template_env)
        pubs_template = template_env.get_template('science/publications.html')
        params['title'] = 'Publications'
        params['self_path'] = '/publications'
        open_graph = {
            'description': 'This is an up-to-date list of all my academic publications. Every article is available to download for free.'
        }
        extra_head = ['<link rel="alternate" type="application/rss+xml" href="/publications.xml">']
        output = pubs_template.render(publications=pubs, open_graph=open_graph, extra_head=extra_head, **params)
        sort_into_structure(params['title'], params['current_site'] + '/publications', 'publications', 10, params['structure'])
        add_to_build(output, 'publications.html', params)
        index_template = template_env.get_template('science/index.html')
        params['title'] = 'Science'
        params['self_path'] = ''
        open_graph = {
            'description': 'In this part of the website you can find information about my research and teaching activities. You can take a look at my academic publications, student projects and theses I have supervised, as well as my academic community involvement.'
        }
        index_output = index_template.render(publications=pubs[0:3], open_graph=open_graph, **params)
        add_to_build(index_output, 'index.html', params)
        feed_template = template_env.get_template('science/publications.xml')
        feed_output = feed_template.render(pubs=pubs, **params)
        add_to_build(feed_output, 'publications.xml', params)

        with open(os.path.join(params['data_root'], 'content', 'science', 'student_theses.json')) as fp:
            student_theses = json.load(fp)
        student_theses = [student_theses[id] for id in student_theses]
        student_theses.sort(key=lambda t: t['year']+t['month']+t['day'])
        student_theses.reverse()
        source_dir = os.path.join(params['data_root'], 'content', 'science')
        student_theses_cache_dir = os.path.join(params['data_root'], 'cache', 'student_theses')
        if not os.path.isdir(student_theses_cache_dir):
            os.makedirs(student_theses_cache_dir)
        for thesis in student_theses:
            pdf_path = os.path.join(source_dir, str(thesis['url_id']) + '.pdf')
            if not os.path.isfile(pdf_path):
                continue
            if thesis['enable_download']:
                add_to_build(pdf_path, thesis['url_id'] + '.pdf', params)
            thumbnail_base_size = 400
            for size_factor in [1, 2, 3]:
                thumbnail_filename = thesis['url_id'] + '_thumbnail.'
                if size_factor != 1:
                    thumbnail_filename = thumbnail_filename[:-1] + '-' + str(size_factor) + 'x.'
                thumbnail_path = os.path.join(student_theses_cache_dir, thumbnail_filename)
                if not os.path.isfile(thumbnail_path + 'png'):
                    thumbnail_interim = thumbnail_path[:-1] + '-precrush.png'
                    subprocess.run(['convert', '-density', '600', pdf_path+'[0]',
                                    '-alpha', 'remove', '-resize', str(thumbnail_base_size * size_factor), thumbnail_interim])
                    image = PIL.Image.open(thumbnail_interim)
                    image = image.convert('RGB')
                    image_grayscale = image.convert('L').convert('RGB')
                    difference = PIL.ImageChops.difference(image, image_grayscale)
                    tint_sum = 0
                    for pixel in difference.getdata():
                        if pixel != (0, 0, 0):
                            tint_sum += pixel[0] + pixel[1] + pixel[2]
                    tinted_quotient = tint_sum / (image.width * image.height)
                    if tinted_quotient < 0.1:
                        image = image.convert('L')
                    image.save(thumbnail_interim)
                    subprocess.run(['pngcrush', thumbnail_interim, thumbnail_path + 'png'])
                    os.remove(thumbnail_interim)
                    thesis['thumbnail_size'] = list(image.size)
                add_to_build(thumbnail_path + 'png', os.path.join('assets', thumbnail_filename + 'png'), params)
                if not os.path.isfile(thumbnail_path + 'webp'):
                    subprocess.run(['cwebp', '-preset', 'text', '-q', '35', '-m', '6', '-noalpha', thumbnail_path + 'png', '-o', thumbnail_path + 'webp'])
                add_to_build(thumbnail_path + 'webp', os.path.join('assets', thumbnail_filename + 'webp'), params)
                if not os.path.isfile(thumbnail_path + 'avif'):
                    subprocess.run(['cavif', '--quality', '35', thumbnail_path + 'png', '-o', thumbnail_path + 'avif'])
                add_to_build(thumbnail_path + 'avif', os.path.join('assets', thumbnail_filename + 'avif'), params)
            if 'thumbnail_size' not in thesis:
                image = PIL.Image.open(thumbnail_path + 'png')
                thesis['thumbnail_size'] = list(image.size)
            thesis['has_thumbnail'] = True
        teaching_template = template_env.get_template('science/teaching.html')
        params['title'] = 'Teaching'
        params['self_path'] = '/teaching'
        open_graph = {
            'description': 'This is an overview of some of my current and past teaching activity. See below for some interesting student projects taught or assisted by me, take a look at theses I supervised, get an overview of my teaching qualifications or refer to a complete list of courses I have taught.'
        }
        output = teaching_template.render(student_theses=student_theses, open_graph=open_graph, **params)
        sort_into_structure(params['title'], params['current_site'] + '/teaching', 'teaching', 20, params['structure'])
        sort_into_structure('Student Projects', params['current_site'] + '/teaching/student_projects', 'teaching#student_projects', 20, params['structure'])
        add_to_build(output, 'teaching.html', params)

    if site['name'] == 'Software':
        with open(os.path.join(params['data_root'], 'content', 'software', 'projects.json')) as fp:
            projects = json.load(fp)
        projects = [projects[id] for id in projects]
        projects.sort(key=lambda p: p['title'].lower())
        template = template_env.get_template('software/index.html')
        params['self_path'] = ''
        open_graph = {
            'description': 'This is where you can find resources about my various software projects. These are exclusively projects I consider “mine,” so anything I have worked on as part of a bigger team or have only made minor contributions to will not be listed here.'
        }
        output = template.render(projects=projects, open_graph=open_graph, **params)
        add_to_build(output, 'index.html', params)

        template = template_env.get_template('software/projects.html')
        category_data = {
            'major': {
                'title': 'Major Projects',
                'url_segment': 'major_projects',
                'description': 'These are some software development projects of mine that are on the larger end of the scope or more generally usable than my more specific tools.',
                'weight': 1
            },
            'minor': {
                'title': 'Smaller Tools',
                'url_segment': 'smaller_tools',
                'description': 'Over the course of a programmer\'s life, many small or tiny technical solutions for everyday issues get created.',
                'weight': 2
            }
        }
        for category in ['major', 'minor']:
            params['title'] = category_data[category]['title']
            url_segment = category_data[category]['url_segment']
            params['self_path'] = '/' + url_segment
            open_graph = {
                'description': category_data[category]['description']
            }
            output = template.render(projects=projects, project_category=category, open_graph=open_graph, **params)
            sort_into_structure(params['title'], params['current_site'] + '/' + url_segment, url_segment, category_data[category]['weight'], params['structure'])
            add_to_build(output, url_segment + '.html', params)

        weight = 1
        for proj in projects:
            template = template_env.get_template('software/project.html')
            params['title'] = proj['title']
            params['self_path'] = '/' + proj['url_id']
            css = ''
            if proj['url_id'] == 'readerbar':
                css = 'readerbar.css'
            open_graph = {
                'description': proj['summary'],
                'image': params['protocol'] + params['hostname'] + params['hostname_suffix'] + '/assets/' + proj['logo'][-1],
                'image:alt': proj['title'] + ' logo'
            }
            output = template.render(proj=proj, css=css, open_graph=open_graph, **params)
            sort_into_structure(params['title'], params['current_site'] + '/' + category_data[proj['category']]['url_segment'] + '/' + proj['url_id'], proj['url_id'], weight, params['structure'])
            weight += 1
            add_to_build(output, proj['url_id'] + '.html', params)

    if site['name'] == 'Media':
        with open(os.path.join(params['data_root'], 'content', 'media', 'games.json')) as fp:
            games = json.load(fp)
        games = [games[id] for id in games]
        games.sort(key=lambda p: p['date'])
        games.reverse()
        template = template_env.get_template('media/games.html')
        params['title'] = 'Games'
        params['self_path'] = '/games'
        open_graph = {
            'description': 'I am happy to have been partially or wholly responsible for several completed game development projects that have brought people fun and laughter. On this page you can find a list of the finished ones in order of recency.'
        }
        output = template.render(projects=games, open_graph=open_graph, **params)
        sort_into_structure(params['title'], params['current_site'] + '/games', 'games', 1, params['structure'])
        add_to_build(output, 'games.html', params)

        weight = 1
        for proj in games:
            template = template_env.get_template('media/game.html')
            params['title'] = proj['title']
            params['self_path'] = '/' + proj['url_id']
            proj['pretty_date'] = pretty_format(proj['date'])
            css = ''
            if 'player' in proj:
                if proj['player']['type'] == 'raw':
                    proj['player']['content'] = fread(os.path.join(params['data_root'], 'content', 'media', proj['player']['file']))
                css = 'player.css'
            open_graph = {
                'description': proj['summary'],
                'image': params['protocol'] + params['hostname'] + params['hostname_suffix'] + '/assets/' + proj['logo'][-1],
                'image:alt': proj['title'] + ' logo'
            }
            output = template.render(proj=proj, css=css, open_graph=open_graph, **params)
            sort_into_structure(params['title'], params['current_site'] + '/games/' + proj['url_id'], proj['url_id'], weight, params['structure'])
            weight += 1
            add_to_build(output, proj['url_id'] + '.html', params)

        with open(os.path.join(params['data_root'], 'content', 'media', 'videos.json')) as fp:
            videos = json.load(fp)
        videos = [videos[id] for id in videos]
        videos.sort(key=lambda v: v['date']+v['title'])
        template = template_env.get_template('media/videos.html')
        params['title'] = 'Videos: Working with LaTeX'
        params['self_path'] = '/videos'
        open_graph = {
            'description': 'In 2011 and 2012 I created a series of video tutorials about using LaTeX in an academic environment, especially as a student. They were accompanied by a seminar where students were able to attend and ask questions.',
            'image': params['protocol'] + params['hostname'] + params['hostname_suffix'] + '/assets/arbeiten_mit_latex_ankuendigung_poster.png',
            'image:alt': 'Working with LaTeX logo'
        }
        output = template.render(videos=videos, open_graph=open_graph, **params)
        sort_into_structure(params['title'], params['current_site'] + '/videos', 'videos', 2, params['structure'])
        add_to_build(output, 'videos.html', params)

        weight = 1
        for video in videos:
            template = template_env.get_template('media/video.html')
            params['title'] = video['title']
            params['self_path'] = '/' + video['url_id']
            video['pretty_date'] = pretty_format(video['date'])
            open_graph = {
                'description': 'Arbeiten mit LaTeX – ' + video['title'],
                'image': params['protocol'] + params['hostname'] + params['hostname_suffix'] + '/assets/' + video['url_id'] + '_poster.png',
                'image:alt': video['title'] + ' (starting slide)',
                'type': 'video.episode'
            }
            output = template.render(video=video, open_graph=open_graph, **params)
            sort_into_structure(params['title'], params['current_site'] + '/videos/' + video['url_id'], video['url_id'], weight, params['structure'])
            weight += 1
            add_to_build(output, video['url_id'] + '.html', params)

        with open(os.path.join(params['data_root'], 'content', 'media', 'misc.json')) as fp:
            miscs = json.load(fp)
        miscs = [miscs[id] for id in miscs]
        miscs.sort(key=lambda p: p['title'])
        template = template_env.get_template('media/miscs.html')
        params['title'] = 'Miscellaneous'
        params['self_path'] = '/misc'
        open_graph = {
            'description': 'From time to time I create something that doesn\'t fit neatly into any of the other categories. This is where you can find the more irregular results of my creative moments.'
        }
        output = template.render(projects=miscs, open_graph=open_graph, **params)
        sort_into_structure(params['title'], params['current_site'] + '/misc', 'misc', 3, params['structure'])
        add_to_build(output, 'misc.html', params)

        weight = 1
        for misc in miscs:
            template = template_env.get_template('media/misc.html')
            params['title'] = misc['title']
            params['self_path'] = '/' + misc['url_id']
            misc['pretty_date'] = pretty_format(misc['date'])
            open_graph = {
                'description': misc['summary'],
                'image': params['protocol'] + params['hostname'] + params['hostname_suffix'] + '/assets/' + misc['logo'][-1],
                'image:alt': misc['title'] + ' logo'
            }
            output = template.render(proj=misc, open_graph=open_graph, **params)
            sort_into_structure(params['title'], params['current_site'] + '/misc/' + misc['url_id'], misc['url_id'], weight, params['structure'])
            weight += 1
            add_to_build(output, misc['url_id'] + '.html', params)

        template = template_env.get_template('media/index.html')
        params['title'] = 'Media'
        params['self_path'] = ''
        open_graph = {
            'description': 'This part of the website contains some of my non-scientific creative output, which is mostly hobby projects. These lists are not exhaustive and for each category there are things I haven\'t published, but I tried to add everything that could potentially be interesting to look at or read about.'
        }
        output = template.render(games=games, miscs=miscs, open_graph=open_graph, **params)
        add_to_build(output, 'index.html', params)

    tint = params['accent_color']
    if not tint.startswith('#'):
        raise ValueError('Failed to parse accent color: ' + tint)
    tint = tint[1:]
    if len(tint) == 3:
        tint = ''.join([2*c for c in tint])
    red = int(tint[0:2], 16)
    green = int(tint[2:4], 16)
    blue = int(tint[4:6], 16)
    favicon_cache_dir = os.path.join(params['data_root'], 'cache', 'favicon')
    if not os.path.isdir(favicon_cache_dir):
        os.makedirs(favicon_cache_dir)
    favicon_cache = os.path.join(favicon_cache_dir, site['name'] + '-original.png')
    if not os.path.isfile(favicon_cache):
        favicon_large = PIL.Image.open(os.path.join(params['data_root'], 'templates', 'favicon.png'))
        favicon_large = PIL.ImageChops.multiply(favicon_large, PIL.Image.new('RGBA', favicon_large.size, (red, green, blue)))
        favicon_large.save(favicon_cache)
    else:
        favicon_large = PIL.Image.open(favicon_cache)
    favicon_ico_cache = os.path.join(favicon_cache_dir, site['name'] + '.ico')
    if not os.path.isfile(favicon_ico_cache):
        interim = favicon_cache[:-4]+'-precrush.png'
        favicon = favicon_large.resize((32, 32), resample=PIL.Image.LANCZOS)
        favicon.save(favicon_ico_cache, sizes=[(16, 16), (24, 24), (32, 32)])
    add_to_build(favicon_ico_cache, 'favicon.ico', params)
    for size in [32, 128, 152, 167, 180, 192, 196, 600]:
        favicon_cache = os.path.join(favicon_cache_dir, site['name'] + '-' + str(size) + '.png')
        if not os.path.isfile(favicon_cache):
            interim = favicon_cache[:-4]+'-precrush.png'
            favicon = favicon_large.resize((size, size), resample=PIL.Image.LANCZOS)
            favicon.save(favicon_cache[:-4]+'-precrush.png')
            subprocess.run(['pngcrush', interim, favicon_cache])
            os.remove(interim)
        add_to_build(favicon_cache, os.path.join('assets', 'favicon-' + str(size) + '.png'), params)
    illustrations_cache_dir = os.path.join(params['data_root'], 'cache', 'illustrations')
    if not os.path.isdir(illustrations_cache_dir):
        os.makedirs(illustrations_cache_dir)
    error_404_full = os.path.join(illustrations_cache_dir, 'error-404-' + site['name'] + '-full.png')
    if not os.path.isfile(error_404_full):
        error_404_base = PIL.Image.open(os.path.join(params['data_root'], 'templates', 'error_404_base.png'))
        error_404_overlay = PIL.Image.open(os.path.join(params['data_root'], 'templates', 'error_404_overlay.png'))
        error_404_overlay = PIL.ImageChops.multiply(error_404_overlay, PIL.Image.new('RGBA', error_404_overlay.size, (red, green, blue)))
        error_404_illustration = PIL.Image.alpha_composite(error_404_base, error_404_overlay)
        error_404_illustration.save(error_404_full)
    error_404_optimized = os.path.join(illustrations_cache_dir, 'error-404-' + site['name'] + '-optimized.')
    if not os.path.isfile(error_404_optimized + 'png'):
        subprocess.run(['convert', error_404_full, '+dither', '-colors', '256', '-alpha', 'background', 'PNG8:' + error_404_optimized + 'interim.png'])
        subprocess.run(['pngcrush', error_404_optimized + 'interim.png', error_404_optimized + 'png'])
        os.remove(error_404_optimized + 'interim.png')
    add_to_build(error_404_optimized + 'png', os.path.join('assets', 'error_404.png'), params)
    if not os.path.isfile(error_404_optimized + 'webp'):
        subprocess.run(['cwebp', '-preset', 'drawing', '-q', '55', '-m', '6', error_404_full, '-o', error_404_optimized + 'webp'])
    add_to_build(error_404_optimized + 'webp', os.path.join('assets', 'error_404.webp'), params)


def get_sitemap_entries(structure, base_url):
    entries = []
    for id in structure:
        item = structure[id]
        path = item['path']
        if '#' in path:
            path = path[:path.index('#')]
        if path == 'imprint':
            # Try not to have search engines list the imprint
            continue
        if ':' not in path:
            path = base_url + path
        if path not in entries:
            entries.append(path)
        if 'children' in item:
            for child_entry in get_sitemap_entries(item['children'], base_url):
                if child_entry not in entries:
                    entries.append(child_entry)
    return entries

def main(argv):

    params = json.loads(fread('params.json'))
    params['current_year'] = datetime.datetime.utcnow().year
    params['rfc_2822_now'] = rfc_2822_format(datetime.datetime.utcnow())
    structure = {}
    build_path = os.path.join(params['data_root'], 'build')

    if 'clean' in argv:
        if os.path.isdir(build_path):
            for name in os.listdir(build_path):
                item = os.path.join(build_path, name)
                if os.path.isdir(item):
                    shutil.rmtree(item)
                else:
                    os.remove(item)
    else:
        if 'deploy' in argv:
            build_target = 'prod'
        else:
            build_target = 'dev'
        params['build_target'] = build_target
        params.update(params['env'][build_target])
        del params['env']

        templates_path = os.path.join(params['data_root'], 'templates')
        template_env = jinja2.Environment(loader=jinja2.FileSystemLoader(templates_path))

        weight = 1
        for site in params['sites']:
            site_params = copy.deepcopy(params)
            del site_params['target_root']
            site_params['structure'] = structure
            site_params['site_dir'] = site['name'].lower()
            site_params['title'] = site['name']
            site_params['current_site'] = site['name']
            site_params['hostname'] = site['hostname']
            site_params['accent_color'] = site['accent_color']
            structure_title = site_params['title']
            if structure_title == 'Me':
                structure_title = 'About Me'
            sort_into_structure(structure_title, site_params['current_site'], params['protocol']+site_params['hostname']+site_params['hostname_suffix'], weight, structure)
            sort_into_structure('Sitemap', site_params['current_site'] + '/sitemap', 'sitemap', 999, structure)
            compile_site(site, site_params)
            template = template_env.get_template('sitemap.xml')
            entries = get_sitemap_entries({ site['name']: structure[site['name']] }, params['protocol']+site['hostname']+params['hostname_suffix']+'/')
            entries.sort()
            output = template.render(entries=entries)
            add_to_build(output, 'sitemap.xml', site_params)
            weight += 1

        cleanup_structure(structure, collate_common=True)

        for site in params['sites']:
            site_params = copy.deepcopy(params)
            del site_params['target_root']
            site_params['structure'] = structure
            site_params['site_dir'] = site['name'].lower()
            site_params['title'] = 'Sitemap'
            site_params['current_site'] = site['name']
            site_params['hostname'] = site['hostname']
            site_params['accent_color'] = site['accent_color']
            template = template_env.get_template('sitemap.html')
            params['title'] = 'Sitemap'
            params['self_path'] = '/sitemap'
            open_graph = {
                'description': 'This is a human-readable complete sitemap for this website.'
            }
            output = template.render(open_graph=open_graph, **site_params)
            add_to_build(output, 'sitemap.html', site_params)

        cmd = ['rsync', '--progress', '--recursive', '--copy-links', '--safe-links', '--times', '--perms', '--delete', os.path.join(build_path, params['build_target']) + '/', params['target_root'] + '/']
        subprocess.run(cmd)

# Test parameter to be set temporarily by unit tests.
_test = None


if __name__ == '__main__':
    main(sys.argv)

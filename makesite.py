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


import collections
import copy
import datetime
import distutils.dir_util
import glob
import htmlmin
import jinja2
import json
import os
import PIL.Image
import PIL.ImageChops
import re
import rcssmin
import rjsmin
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
    for match in re.finditer(r'\s*<!--\s*(.+?)\s*:\s*(.+?)\s*-->\s*|.+', text):
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


def add_to_build(source, target, params):
    link_if_bigger_than = 4 * 1024 * 1024
    build_permissions = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH
    build_path = os.path.join(params['data_root'], 'build')
    if target.startswith('/'):
        target = target[1:]
    target = os.path.join(params['site_dir'], target)
    if target.endswith('.html'):
        if os.path.isfile(source):
            source = fread(source)
        source = htmlmin.minify(source, remove_empty_space=True, remove_optional_attribute_quotes=False)
    if target.endswith('.css'):
        if os.path.isfile(source):
            source = fread(source)
        source = rcssmin.cssmin(source)
    if target.endswith('.js'):
        if os.path.isfile(source):
            source = fread(source)
        source = rjsmin.jsmin(source)
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
                shutil.copy2(source, target_path)
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
            if source_stat.st_mtime != target_stat.st_mtime or source_stat.st_size != target_stat.st_size:
                log('Adding {} from {} ...'.format(target, source))
                if os.path.getsize(source) > link_if_bigger_than:
                    os.symlink(source, target_path)
                    os.chmod(target_path, build_permissions)
                else:
                    shutil.copy2(source, target_path)
                    os.chmod(target_path, build_permissions)
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

        page_params = dict(params, **content)

        items.append(content)

        dst_path = render(destination, **page_params)
        output = template.render(**page_params)

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
        pub_files = glob.glob(os.path.join(source_dir, str(pub['id'])+'.*'))
        for pub_file in pub_files:
            extension = os.path.splitext(pub_file)[1]
            if extension == '.html':
               pub['content_html'] = fread(pub_file)
               continue
            add_to_build(pub_file, pub['url_id'] + extension, params)
            pub['has_download_'+extension[1:]] = True
            if extension == '.pdf':
                thumbnail_path = os.path.join(cache_dir, pub['url_id'] + '_thumbnail.png')
                if not os.path.isfile(thumbnail_path):
                    thumbnail_interim = thumbnail_path[:-4] + '-precrush.png'
                    subprocess.run(['convert', '-density', '600', pub_file+'[0]',
                                    '-alpha', 'remove', '-resize', '400', thumbnail_interim])
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
                    subprocess.run(['pngcrush', thumbnail_interim, thumbnail_path])
                    os.remove(thumbnail_interim)
                add_to_build(thumbnail_path, os.path.join('assets', pub['url_id'] + '_thumbnail.png'), params)
                pub['has_thumbnail'] = True
                if not os.path.isfile(os.path.join(cache_dir, pub['url_id'] + '_page1.svg')):
                    svg_path = os.path.join(cache_dir, pub['url_id'] + '_page%d.svg')
                    subprocess.run(['pdf2svg', pub_file, svg_path, 'all'])
                svg_pages = glob.glob(os.path.join(cache_dir, pub['url_id'] + '_page*.svg'))
                for svg in svg_pages:
                    add_to_build(svg, os.path.join('assets', os.path.basename(svg)), params)
                if len(svg_pages) > 0:
                    pub['content_svg'] = len(svg_pages)

        bibtex_data = collections.OrderedDict()
        bibtex_data['author'] = ' AND '.join(pub['authors'])
        bibtex_data['title'] = pub['title']
        bibtex_data['year'] = pub['year']
        bibtex_id = pub['authors'][0].split(', ')[0]
        if len(pub['authors']) > 1:
            bibtex_id += ''.join(name[0] for name in pub['authors'][1:])
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
        output = pub_template.render(publication=pub, css='publication.css', **params)
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

    page_template = template_env.get_template('page.html')
    page_list = glob.glob(os.path.join(site_content_path, '*.html'))
    for candidate in glob.glob(os.path.join(content_path, '*.html')):
        if candidate.replace(content_path, site_content_path) not in page_list:
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
            pub['rfc_2822_date'] = rfc_2822_format(datetime.datetime(int(pub['year']), int(pub['month']), int(pub['day']), 0, 0, 0))
        prepare_pub_files(pubs, params, template_env)
        pubs_template = template_env.get_template('science/publications.html')
        params['title'] = 'Publications'
        output = pubs_template.render(publications=pubs, **params)
        sort_into_structure(params['title'], params['current_site'] + '/publications', 'publications', 10, params['structure'])
        add_to_build(output, 'publications.html', params)
        index_template = template_env.get_template('science/index.html')
        params['title'] = 'Science'
        index_output = index_template.render(publications=pubs[0:3], **params)
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
            thumbnail_path = os.path.join(student_theses_cache_dir, thesis['url_id'] + '_thumbnail.png')
            if not os.path.isfile(thumbnail_path):
                interim = thumbnail_path[:-4]+'-precrush.png'
                subprocess.run(['convert', '-density', '600', pdf_path+'[0]',
                                '-alpha', 'remove', '-resize', '400', interim])
                subprocess.run(['pngcrush', interim, thumbnail_path])
                os.remove(interim)
            add_to_build(thumbnail_path, os.path.join('assets', thesis['url_id'] + '_thumbnail.png'), params)
            thesis['has_thumbnail'] = True
        teaching_template = template_env.get_template('science/teaching.html')
        params['title'] = 'Teaching'
        output = teaching_template.render(student_theses=student_theses, **params)
        sort_into_structure(params['title'], params['current_site'] + '/teaching', 'teaching', 20, params['structure'])
        sort_into_structure('Student Projects', params['current_site'] + '/teaching/student_projects', 'teaching#student_projects', 20, params['structure'])
        add_to_build(output, 'teaching.html', params)

    if site['name'] == 'Software':
        with open(os.path.join(params['data_root'], 'content', 'software', 'projects.json')) as fp:
            projects = json.load(fp)
        projects = [projects[id] for id in projects]
        projects.sort(key=lambda p: p['title'].lower())
        template = template_env.get_template('software/index.html')
        output = template.render(projects=projects, **params)
        add_to_build(output, 'index.html', params)

        template = template_env.get_template('software/projects.html')
        category_data = {
            'major': {
                'title': 'Major Projects',
                'url_segment': 'major_projects',
                'weight': 1
            },
            'minor': {
                'title': 'Smaller Tools',
                'url_segment': 'smaller_tools',
                'weight': 2
            }
        }
        for category in ['major', 'minor']:
            params['title'] = category_data[category]['title']
            output = template.render(projects=projects, project_category=category, **params)
            url_segment = category_data[category]['url_segment']
            sort_into_structure(params['title'], params['current_site'] + '/' + url_segment, url_segment, category_data[category]['weight'], params['structure'])
            add_to_build(output, url_segment + '.html', params)

        weight = 1
        for proj in projects:
            template = template_env.get_template('software/project.html')
            params['title'] = proj['title']
            output = template.render(proj=proj, **params)
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
        output = template.render(projects=games, **params)
        sort_into_structure(params['title'], params['current_site'] + '/games', 'games', 1, params['structure'])
        add_to_build(output, 'games.html', params)

        weight = 1
        for proj in games:
            template = template_env.get_template('media/game.html')
            params['title'] = proj['title']
            proj['pretty_date'] = pretty_format(proj['date'])
            css = ''
            if 'player' in proj:
                if proj['player']['type'] == 'raw':
                    proj['player']['content'] = fread(os.path.join(params['data_root'], 'content', 'media', proj['player']['file']))
                css = 'player.css'
            output = template.render(proj=proj, css=css, **params)
            sort_into_structure(params['title'], params['current_site'] + '/games/' + proj['url_id'], proj['url_id'], weight, params['structure'])
            weight += 1
            add_to_build(output, proj['url_id'] + '.html', params)

        with open(os.path.join(params['data_root'], 'content', 'media', 'videos.json')) as fp:
            videos = json.load(fp)
        videos = [videos[id] for id in videos]
        videos.sort(key=lambda v: v['date']+v['title'])
        template = template_env.get_template('media/videos.html')
        params['title'] = 'Videos: Working with LaTeX'
        output = template.render(videos=videos, **params)
        sort_into_structure(params['title'], params['current_site'] + '/videos', 'videos', 2, params['structure'])
        add_to_build(output, 'videos.html', params)

        weight = 1
        for video in videos:
            template = template_env.get_template('media/video.html')
            params['title'] = video['title']
            video['pretty_date'] = pretty_format(video['date'])
            output = template.render(video=video, **params)
            sort_into_structure(params['title'], params['current_site'] + '/videos/' + video['url_id'], video['url_id'], weight, params['structure'])
            weight += 1
            add_to_build(output, video['url_id'] + '.html', params)

        with open(os.path.join(params['data_root'], 'content', 'media', 'misc.json')) as fp:
            miscs = json.load(fp)
        miscs = [miscs[id] for id in miscs]
        miscs.sort(key=lambda p: p['title'])
        template = template_env.get_template('media/miscs.html')
        params['title'] = 'Miscellaneous'
        output = template.render(projects=miscs, **params)
        sort_into_structure(params['title'], params['current_site'] + '/misc', 'misc', 3, params['structure'])
        add_to_build(output, 'misc.html', params)

        weight = 1
        for misc in miscs:
            template = template_env.get_template('media/misc.html')
            params['title'] = misc['title']
            misc['pretty_date'] = pretty_format(misc['date'])
            output = template.render(proj=misc, **params)
            sort_into_structure(params['title'], params['current_site'] + '/misc/' + misc['url_id'], misc['url_id'], weight, params['structure'])
            weight += 1
            add_to_build(output, misc['url_id'] + '.html', params)

        template = template_env.get_template('media/index.html')
        params['title'] = 'Media'
        output = template.render(games=games, miscs=miscs, **params)
        add_to_build(output, 'index.html', params)

    additional_templates = ['main.css', 'robots.txt']
    for additional_template in additional_templates:
        template = template_env.get_template(additional_template)
        output = template.render(**params)
        add_to_build(output, additional_template, params)

    favicon_cache_dir = os.path.join(params['data_root'], 'cache', 'favicon')
    if not os.path.isdir(favicon_cache_dir):
        os.makedirs(favicon_cache_dir)
    favicon_cache = os.path.join(favicon_cache_dir, site['name'] + '-original.png')
    if not os.path.isfile(favicon_cache):
        tint = params['accent_color']
        if not tint.startswith('#'):
            raise ValueError('Failed to parse accent color: ' + tint)
        tint = tint[1:]
        if len(tint) == 3:
            tint = ''.join([2*c for c in tint])
        red = int(tint[0:2], 16)
        green = int(tint[2:4], 16)
        blue = int(tint[4:6], 16)
        favicon_large = PIL.Image.open(os.path.join(params['data_root'], 'templates', 'favicon.png'))
        favicon_large = PIL.ImageChops.multiply(favicon_large, PIL.Image.new('RGBA', favicon_large.size, (red, green, blue)))
        favicon_large.save(favicon_cache)
    else:
        favicon_large = PIL.Image.open(favicon_cache)
    for size in [32, 128, 152, 167, 180, 192, 196]:
        favicon_cache = os.path.join(favicon_cache_dir, site['name'] + '-' + str(size) + '.png')
        if not os.path.isfile(favicon_cache):
            interim = favicon_cache[:-4]+'-precrush.png'
            favicon = favicon_large.resize((size, size), resample=PIL.Image.LANCZOS)
            favicon.save(favicon_cache[:-4]+'-precrush.png')
            subprocess.run(['pngcrush', interim, favicon_cache])
            os.remove(interim)
        add_to_build(favicon_cache, os.path.join('assets', 'favicon-' + str(size) + '.png'), params)


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
            params.update(params['env']['prod'])
        else:
            params.update(params['env']['dev'])
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
            output = template.render(**site_params)
            add_to_build(output, 'sitemap.html', site_params)

        cmd = ['rsync', '--progress', '--recursive', '--copy-links', '--safe-links', '--times', '--perms', '--delete', build_path + '/', params['target_root'] + '/']
        subprocess.run(cmd)

# Test parameter to be set temporarily by unit tests.
_test = None


if __name__ == '__main__':
    main(sys.argv)

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

import copy
import datetime
import distutils.dir_util
import glob
import jinja2
import json
import os
import re
import shutil
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
    build_path = os.path.join(params['data_root'], 'build')
    if target.startswith('/'):
        target = target[1:]
    target = os.path.join(params['site_dir'], target)
    if not os.path.isfile(os.path.join(build_path, target)):
        target_dir = os.path.dirname(os.path.join(build_path, target))
        if not os.path.isdir(target_dir):
            os.makedirs(target_dir)
        # check if source is a path or direct file contents
        if not os.path.isfile(source):
            log('Adding {} from inline data ...'.format(target))
            fwrite(os.path.join(build_path, target), source)
        else:
            log('Adding {} from {} ...'.format(target, source))
            if os.path.getsize(source) > link_if_bigger_than:
                os.symlink(source, os.path.join(build_path, target))
            else:
                shutil.copy2(source, os.path.join(build_path, target))
    else:
        target_stat = os.stat(os.path.join(build_path, target))
        if not os.path.isfile(source):
            target_content = fread(os.path.join(build_path, target))
            if source != target_content:
                log('Adding {} from inline data ...'.format(target))
                fwrite(os.path.join(build_path, target), source)
            else:
                # log('Skipping {} - existing file is identical'.format(target))
                pass
        else:
            source_stat = os.stat(source)
            if source_stat.st_mtime != target_stat.st_mtime or source_stat.st_size != target_stat.st_size:
                log('Adding {} from {} ...'.format(target, source))
                if os.path.getsize(source) > link_if_bigger_than:
                    os.symlink(source, os.path.join(build_path, target))
                else:
                    shutil.copy2(source, os.path.join(build_path, target))
            else:
                # log('Skipping {} - existing file is identical'.format(target))
                pass


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

        #log('Rendering {} ...', dst_path)
        add_to_build(output, dst_path, params)

    return sorted(items, key=lambda x: x['date'], reverse=True)


def prepare_pub_files(pubs, params, template_env):
    source_dir = os.path.join(params['data_root'], 'content', 'science')
    cache_dir = os.path.join(params['data_root'], 'cache')
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
                    subprocess.run(['convert', '-density', '600', pub_file+'[0]',
                                    '-alpha', 'remove', '-resize', '600', thumbnail_path])
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
        pub_template = template_env.get_template('science/publication-page.html')
        params['title'] = pub['title']
        output = pub_template.render(publication=pub, css='publication.css', **params)
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

    # Load templates.
    templates_path = os.path.join(params['data_root'], 'templates')

    template_env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(templates_path)
    )

    # Create site pages.
    content_path = os.path.join(params['data_root'], 'content', 'all')
    site_content_path = os.path.join(params['data_root'], 'content', site['name'].lower())

    page_template = template_env.get_template('page.html')
    page_list = glob.glob(os.path.join(site_content_path, '*.html'))
    for candidate in glob.glob(os.path.join(content_path, '*.html')):
        if candidate.replace(content_path, site_content_path) not in page_list:
            page_list.append(candidate)
    make_pages(page_list, '{{ slug }}.html',
               page_template, **params)

    if site['name'] == 'Science':
        pubs = orcid.get(site['orcid'], os.path.join(params['data_root'], 'cache'))
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
        add_to_build(output, 'publications.html', params)
        feed_template = template_env.get_template('science/publications.xml')
        feed_output = feed_template.render(pubs=pubs, **params)
        add_to_build(feed_output, 'publications.xml', params)

        with open(os.path.join(params['data_root'], 'content', 'science', 'student_theses.json')) as fp:
            student_theses = json.load(fp)
        student_theses = [student_theses[id] for id in student_theses]
        student_theses.sort(key=lambda t: t['year']+t['month']+t['day'])
        student_theses.reverse()
        source_dir = os.path.join(params['data_root'], 'content', 'science')
        cache_dir = os.path.join(params['data_root'], 'cache')
        for thesis in student_theses:
            pdf_path = os.path.join(source_dir, str(thesis['url_id']) + '.pdf')
            if not os.path.isfile(pdf_path):
                continue
            if thesis['enable_download']:
                add_to_build(pdf_path, thesis['url_id'] + '.pdf', params)
            thumbnail_path = os.path.join(cache_dir, thesis['url_id'] + '_thumbnail.png')
            if not os.path.isfile(thumbnail_path):
                subprocess.run(['convert', '-density', '600', pdf_path+'[0]',
                                '-alpha', 'remove', '-resize', '400', thumbnail_path])
            add_to_build(thumbnail_path, os.path.join('assets', thesis['url_id'] + '_thumbnail.png'), params)
            thesis['has_thumbnail'] = True
        teaching_template = template_env.get_template('science/teaching.html')
        params['title'] = 'Teaching'
        output = teaching_template.render(student_theses=student_theses, **params)
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
        for category in ['major', 'minor']:
            if category == 'major':
                params['title'] = 'Major Projects'
            elif category == 'minor':
                params['title'] = 'Smaller Tools'
            output = template.render(projects=projects, project_category=category, **params)
            add_to_build(output, params['title'].lower().replace(' ', '_') + '.html', params)

        for proj in projects:
            template = template_env.get_template('software/project.html')
            params['title'] = proj['title']
            output = template.render(proj=proj, **params)
            add_to_build(output, proj['url_id'] + '.html', params)

    if site['name'] == 'Media':
        with open(os.path.join(params['data_root'], 'content', 'media', 'games.json')) as fp:
            projects = json.load(fp)
        projects = [projects[id] for id in projects]
        projects.sort(key=lambda p: p['date'])
        projects.reverse()
        template = template_env.get_template('media/games.html')
        params['title'] = 'Games'
        output = template.render(projects=projects, **params)
        add_to_build(output, 'games.html', params)

        for proj in projects:
            template = template_env.get_template('media/game.html')
            params['title'] = proj['title']
            output = template.render(proj=proj, **params)
            add_to_build(output, proj['url_id'] + '.html', params)

        with open(os.path.join(params['data_root'], 'content', 'media', 'videos.json')) as fp:
            videos = json.load(fp)
        videos = [videos[id] for id in videos]
        videos.sort(key=lambda v: v['date']+v['title'])
        template = template_env.get_template('media/videos.html')
        params['title'] = 'Videos: Working with LaTeX'
        output = template.render(videos=videos, **params)
        add_to_build(output, 'videos.html', params)

        for video in videos:
            template = template_env.get_template('media/video.html')
            params['title'] = video['title']
            output = template.render(video=video, **params)
            add_to_build(output, video['url_id'] + '.html', params)

        with open(os.path.join(params['data_root'], 'content', 'media', 'misc.json')) as fp:
            projects = json.load(fp)
        projects = [projects[id] for id in projects]
        projects.sort(key=lambda p: p['title'])
        template = template_env.get_template('media/miscs.html')
        params['title'] = 'Miscellaneous'
        output = template.render(projects=projects, **params)
        add_to_build(output, 'misc.html', params)

        for misc in projects:
            template = template_env.get_template('media/misc.html')
            params['title'] = misc['title']
            output = template.render(proj=misc, **params)
            add_to_build(output, misc['url_id'] + '.html', params)

    additional_templates = ['main.css']
    for additional_template in additional_templates:
        template = template_env.get_template(additional_template)
        output = template.render(**params)
        add_to_build(output, additional_template, params)


def main(argv):

    params = json.loads(fread('params.json'))
    params['current_year'] = datetime.datetime.utcnow().year
    params['rfc_2822_now'] = rfc_2822_format(datetime.datetime.utcnow())
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
            params.update(params['env']['dev'])
        else:
            params.update(params['env']['prod'])
        del params['env']

        for site in params['sites']:
            site_params = copy.deepcopy(params)
            del site_params['target_root']
            site_params['site_dir'] = site['name'].lower()
            site_params['title'] = site['name']
            site_params['current_site'] = site['name']
            site_params['hostname'] = site['hostname']
            site_params['accent_color'] = site['accent_color']
            compile_site(site, site_params)

        cmd = ['rsync', '--recursive', '--copy-links', '--safe-links', '--times', '--delete', build_path + '/', params['target_root'] + '/']
        subprocess.run(cmd)

# Test parameter to be set temporarily by unit tests.
_test = None


if __name__ == '__main__':
    main(sys.argv)

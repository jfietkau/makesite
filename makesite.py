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


def truncate(text, words=25):
    """Remove tags and truncate text to the specified number of words."""
    return ' '.join(re.sub('(?s)<.*?>', ' ', text).split()[:words])


def read_headers(text):
    """Parse headers in text and yield (key, value, end-index) tuples."""
    for match in re.finditer(r'\s*<!--\s*(.+?)\s*:\s*(.+?)\s*-->\s*|.+', text):
        if not match.group(1):
            break
        yield match.group(1), match.group(2), match.end()


def rfc_2822_format(date_str):
    """Convert yyyy-mm-dd date string to RFC 2822 format date string."""
    d = datetime.datetime.strptime(date_str, '%Y-%m-%d')
    return d.strftime('%a, %d %b %Y %H:%M:%S +0000')


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


def make_pages(src, dst, template, **params):
    """Generate pages from page content."""
    items = []

    for src_path in glob.glob(src):
        if os.path.basename(src_path)[0].isdigit():
            continue

        content = read_content(src_path)

        page_params = dict(params, **content)

        items.append(content)

        dst_path = render(dst, **page_params)
        output = template.render(**page_params)

        log('Rendering {} => {} ...', src_path, dst_path)
        fwrite(os.path.join(params['target_root'], dst_path), output)

    return sorted(items, key=lambda x: x['date'], reverse=True)


def make_list(posts, dst, template, **params):
    """Generate list page for a blog."""
    items = []
    for post in posts:
        post['summary'] = truncate(post['content'])

    params['posts'] = posts
    dst_path = render(dst, **params)
    output = template.render(**params)

    log('Rendering list => {} ...', dst_path)
    fwrite(os.path.join(params['target_root'], dst_path), output)


def main():

    params = json.loads(fread('params.json'))
    params['current_year'] = datetime.datetime.now().year

    for site in params['sites']:
        site_params = copy.deepcopy(params)
        site_params['target_root'] = os.path.join(site_params['target_root'], site['name'].lower())
        site_params['title'] = site['name']
        site_params['current_site'] = site['name']
        site_params['hostname'] = site['name']
        site_params['accent_color'] = site['accent_color']
        compile_site(site, site_params)

def prepare_pub_files(pubs, params, template_env):
    source_dir = os.path.join(params['data_root'], 'content', 'science')
    cache_dir = os.path.join(params['data_root'], 'cache')
    assets_dir = os.path.join(params['target_root'], 'assets')
    if not os.path.isdir(assets_dir):
        os.makedirs(assets_dir)
    for pub in pubs:
        pub_files = glob.glob(os.path.join(source_dir, str(pub['id'])+'.*'))
        for pub_file in pub_files:
            extension = os.path.splitext(pub_file)[1]
            if extension == '.html':
               pub['content_html'] = fread(pub_file)
               continue
            target_path = os.path.join(params['target_root'], pub['url_id'] + extension)
            shutil.copy2(pub_file, target_path)
            pub['has_download_'+extension[1:]] = True
            if extension == '.pdf':
                thumbnail_path = os.path.join(cache_dir, pub['url_id'] + '_thumbnail.png')
                if not os.path.isfile(thumbnail_path):
                    subprocess.run(['convert', '-density', '600', target_path+'[0]',
                                    '-alpha', 'remove', '-resize', '600', thumbnail_path])
                thumbnail_final_path = os.path.join(params['target_root'], 'assets', pub['url_id'] + '_thumbnail.png')
                shutil.copy2(thumbnail_path, thumbnail_final_path)
                pub['has_thumbnail'] = True
                if not os.path.isfile(os.path.join(cache_dir, pub['url_id'] + '_page1.svg')):
                    svg_path = os.path.join(cache_dir, pub['url_id'] + '_page%d.svg')
                    subprocess.run(['pdf2svg', target_path, svg_path, 'all'])
                svg_pages = glob.glob(os.path.join(cache_dir, pub['url_id'] + '_page*.svg'))
                for svg in svg_pages:
                    svg_final_path = os.path.join(params['target_root'], 'assets', os.path.basename(svg))
                    shutil.copy2(svg, svg_final_path)
                if len(svg_pages) > 0:
                    pub['content_svg'] = len(svg_pages)
        pub_template = template_env.get_template('science/publication-page.html')
        params['title'] = pub['title']
        output = pub_template.render(publication=pub, css='publication.css', **params)
        fwrite(os.path.join(params['target_root'], pub['url_id']+'.html'), output)

def compile_site(site, params):

    # Create a new output directory from scratch.
    if os.path.isdir(params['target_root']):
        try:
            shutil.rmtree(params['target_root'])
        except PermissionError:
            pass

    if not os.path.isdir(params['target_root']):
        os.makedirs(params['target_root'])

    for static_source in ['all', site['name'].lower()]:
        static_path = os.path.join(params['data_root'], 'static', static_source)
        if os.path.isdir(static_path):
            distutils.dir_util.copy_tree(static_path, params['target_root'])

    # Load templates.
    templates_path = os.path.join(params['data_root'], 'templates')

    template_env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(templates_path)
    )

    page_template = template_env.get_template('page.html')
    post_template = template_env.get_template('post.html')
    list_template = template_env.get_template('list.html')
    feed_xml = template_env.get_template('feed.xml')

    # Create site pages.
    content_path = os.path.join(params['data_root'], 'content')
    site_content_path = os.path.join(params['data_root'], 'content', site['name'].lower())

    make_pages(os.path.join(content_path, '*.html'), '{{ slug }}.html',
               page_template, **params)
    make_pages(os.path.join(site_content_path, '*.html'), '{{ slug }}.html',
               page_template, **params)

    if site['name'] == 'Science':
        pubs = orcid.get(site['orcid'], os.path.join(params['data_root'], 'cache'))
        with open(os.path.join(params['data_root'], 'content', 'science', 'publications.json')) as fp:
            metadata = json.load(fp)
        for pub in pubs:
            pub_id = str(pub['id'])
            if pub_id in metadata:
                pub.update(metadata[pub_id])
        prepare_pub_files(pubs, params, template_env)
        pubs_template = template_env.get_template('science/publications.html')
        params['title'] = 'Publications'
        output = pubs_template.render(publications=pubs, **params)
        fwrite(os.path.join(params['target_root'], 'publications.html'), output)

        with open(os.path.join(params['data_root'], 'content', 'science', 'student_theses.json')) as fp:
            student_theses = json.load(fp)
        student_theses = [student_theses[id] for id in student_theses]
        student_theses.sort(key=lambda t: t['year']+t['month']+t['day'])
        student_theses.reverse()
        print(student_theses)
        source_dir = os.path.join(params['data_root'], 'content', 'science')
        cache_dir = os.path.join(params['data_root'], 'cache')
        assets_dir = os.path.join(params['target_root'], 'assets')
        if not os.path.isdir(assets_dir):
            os.makedirs(assets_dir)
        for thesis in student_theses:
            pdf_path = os.path.join(source_dir, str(thesis['url_id']) + '.pdf')
            if not os.path.isfile(pdf_path):
                continue
            target_path = os.path.join(params['target_root'], thesis['url_id'] + '.pdf')
            if thesis['enable_download']:
                shutil.copy2(pdf_path, target_path)
            thumbnail_path = os.path.join(cache_dir, thesis['url_id'] + '_thumbnail.png')
            if not os.path.isfile(thumbnail_path):
                subprocess.run(['convert', '-density', '600', pdf_path+'[0]',
                                '-alpha', 'remove', '-resize', '400', thumbnail_path])
            thumbnail_final_path = os.path.join(params['target_root'], 'assets', thesis['url_id'] + '_thumbnail.png')
            shutil.copy2(thumbnail_path, thumbnail_final_path)
            thesis['has_thumbnail'] = True
        teaching_template = template_env.get_template('science/teaching.html')
        params['title'] = 'Teaching'
        output = teaching_template.render(student_theses=student_theses, **params)
        fwrite(os.path.join(params['target_root'], 'teaching.html'), output)

    # Create blogs.
    blog_posts = make_pages(os.path.join(content_path, 'blog/*.md'),
                            'blog/{{ slug }}.html',
                            post_template, blog='blog', **params)
    news_posts = make_pages(os.path.join(content_path, 'news/*.html'),
                            'news/{{ slug }}.html',
                            post_template, blog='news', **params)

    # Create blog list pages.
    make_list(blog_posts, 'blog/index.html',
              list_template, blog='blog', **params)
    make_list(news_posts, 'news/index.html',
              list_template, blog='news', **params)

    # Create RSS feeds.
    make_list(blog_posts, 'blog/rss.xml',
              feed_xml, blog='blog', **params)
    make_list(news_posts, 'news/rss.xml',
              feed_xml, blog='news', **params)

    additional_templates = ['main.css']
    for additional_template in additional_templates:
        template = template_env.get_template(additional_template)
        result = template.render(**params)
        fwrite(os.path.join(params['target_root'], additional_template), result)


# Test parameter to be set temporarily by unit tests.
_test = None


if __name__ == '__main__':
    main()

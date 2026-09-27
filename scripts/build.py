#!/usr/bin/env python3
"""Build the FRK's printable documents and static part reference site."""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import math
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import Image, ImageDraw
import qrcode
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'reference/parts.json'
LINK = re.compile(r'\[\[([a-z0-9]+)(?:#([a-z0-9-]+))?\]\]')
MODELS = {'135': 'HT 135', '261': 'MS 261', '462': 'MS 462'}
COLORS = {'application': '075985', 'supervision': '92400E', 'secondary': '52525B'}


def identifier(number):
    return re.sub(r'[^a-z0-9]', '', number.lower())


def application_name(app):
    return app['name'] if app['name'].startswith('AV ') else app['name'].lower()


def tex(value):
    replacements = {'\\': r'\textbackslash{}', '&': r'\&', '%': r'\%', '$': r'\$',
                    '#': r'\#', '_': r'\_', '{': r'\{', '}': r'\}',
                    '~': r'\textasciitilde{}', '^': r'\textasciicircum{}',
                    'Ø': r'\O{}', '×': r'\ensuremath{\times}', '–': '--',
                    '—': '---', '’': "'", '©': r'\textcopyright{}'}
    return ''.join(replacements.get(c, c) for c in str(value))


def write(path, content):
    path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = content.encode() if isinstance(content, str) else content
    if not path.exists() or path.read_bytes() != raw:
        path.write_bytes(raw)


def copy(source, target):
    write(target, (ROOT / source).read_bytes())


class Reference:
    def __init__(self, data=DATA, inventory=ROOT / 'frk_items.tsv'):
        self.data = json.loads(Path(data).read_text())
        self.url = self.data['site_url'].rstrip('/')
        self.parts = {}
        for row in csv.reader(Path(inventory).read_text().splitlines(), delimiter='\t', quoting=csv.QUOTE_NONE):
            if len(row) != 8:
                raise ValueError(f'Inventory row must have eight columns: {row!r}')
            number, name, count, models, scope, bin_id, category, packaging = row
            key = identifier(number)
            if key in self.parts:
                raise ValueError(f'Duplicate inventory number: {number}')
            self.parts[key] = dict(id=key, number=number, name=name, count=int(count),
                                   models=models.split(','), scope=scope, bin=bin_id,
                                   category=category, packaging=packaging,
                                   **self.data['parts'].get(key, {}))
        self.validate()

    def validate(self):
        if set(self.parts) != set(self.data['parts']):
            raise ValueError('Reference and inventory part numbers differ')
        if not self.url.startswith('https://'):
            raise ValueError('QR destination must use HTTPS')
        for p in self.parts.values():
            apps = p.get('applications', [])
            if not apps or len({a['id'] for a in apps}) != len(apps):
                raise ValueError(f'Missing or duplicate applications: {p["number"]}')
            if set(a['model'] for a in apps) != set(p['models']):
                raise ValueError(f'Inventory/reference model mismatch: {p["number"]}')
            if p['count'] < 0 or p['scope'] not in {'S1', 'AS1', 'CI1', 'S2', 'CI2'}:
                raise ValueError(f'Invalid count or supervision: {p["number"]}')
            for a in apps:
                figure = ROOT / f'reference/figures/{a["manual"]}-{a["drawing"]:03}.png'
                if not figure.exists() or not a['marks'] or a['quantity'] < 1:
                    raise ValueError(f'Incomplete figure: {p["number"]} / {a["id"]}')
                x, y, w, h = a['crop']
                if min(x, y) < 0 or min(w, h) <= 0 or x+w > 595.28 or y+h > 841.90:
                    raise ValueError(f'Crop outside drawing: {p["number"]} / {a["id"]}')
                for target, anchor in LINK.findall(a['text']):
                    if target not in self.parts:
                        raise ValueError(f'Unknown related part: {target}')
                    if anchor and anchor not in {b['id'] for b in self.parts[target]['applications']}:
                        raise ValueError(f'Unknown related application: {target}#{anchor}')
                if a.get('listed_part') and p.get('status') != 'unconfirmed':
                    raise ValueError('A differing manual number must be explicitly unconfirmed')

    def part_url(self, part_id, anchor=''):
        return f'{self.url}/parts/{part_id}/' + (f'#{anchor}' if anchor else '')

    def source(self, app):
        manual = self.data['manuals'][app['manual']]
        return f'{manual["title"]}, {manual["edition"]}. Drawing p. {app["drawing"]}; parts table p. {app["table"]}.'

    def render_text(self, value, mode):
        escape = tex if mode in {'tex', 'offline'} else html.escape
        chunks = []
        offset = 0
        for match in LINK.finditer(value):
            chunks.append(escape(value[offset:match.start()]))
            key, anchor = match.groups()
            number = self.parts[key]['number']
            if mode == 'offline':
                destination = key + ('-' + anchor if anchor else '')
                chunks.append(r'\hyperlink{' + destination + r'}{\textcolor{frkApplication}{\mbox{\texttt{' + tex(number) + '}}}}')
            elif mode == 'tex':
                chunks.append(r'\href{' + tex(self.part_url(key, anchor)) + r'}{\textcolor{frkApplication}{\mbox{\texttt{' + tex(number) + '}}}}')
            elif mode == 'md':
                target = f'../{key}/README.md' + (f'#{anchor}' if anchor else '')
                chunks.append(f'[{number}]({target})')
            else:
                target = f'../{key}/' + (f'#{anchor}' if anchor else '')
                chunks.append(f'<a class="part-number" href="{target}">{number}</a>')
            offset = match.end()
        chunks.append(escape(value[offset:]))
        return ''.join(chunks)

    def figure(self, p, a):
        source = ROOT / f'reference/figures/{a["manual"]}-{a["drawing"]:03}.png'
        with Image.open(source) as full:
            sx, sy = full.width / 595.2756, full.height / 841.8898
            x, y, w, h = a['crop']
            crop = full.crop((round(x*sx), round(y*sy), round((x+w)*sx), round((y+h)*sy))).convert('RGBA')
        overlay = Image.new('RGBA', crop.size)
        draw = ImageDraw.Draw(overlay)
        rgb = tuple(int(COLORS['application'][i:i+2], 16) for i in (0, 2, 4))
        fill, outline = (*rgb, 31), (*rgb, 235)
        width = max(2, round(1.6*sx))
        for mark in a['marks']:
            if 'ellipse' in mark:
                cx, cy, rx, ry = mark['ellipse']
                bounds = ((cx-rx-x)*sx, (cy-ry-y)*sy, (cx+rx-x)*sx, (cy+ry-y)*sy)
                draw.ellipse(bounds, fill=fill, outline=outline, width=width)
            else:
                points = [((px-x)*sx, (py-y)*sy) for px, py in mark['polygon']]
                draw.polygon(points, fill=fill)
                draw.line(points + points[:1], fill=outline, width=width, joint='curve')
        result = Image.alpha_composite(crop, overlay).convert('RGB')
        output = io.BytesIO()
        result.save(output, format='PNG', optimize=True)
        write(f'parts/{p["id"]}/images/{a["id"]}.png', output.getvalue())

    def assets(self):
        for p in self.parts.values():
            qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
            qr.add_data(self.part_url(p['id']))
            qr.make(fit=True)
            output = io.BytesIO()
            qr.make_image(fill_color='black', back_color='white').save(output, format='PNG')
            write(f'parts/{p["id"]}/images/qr.png', output.getvalue())
            for a in p['applications']:
                self.figure(p, a)

    def preamble(self, margin='0.45in', relative='', book=False):
        return r'''% Generated by scripts/build.py from frk_items.tsv and reference/parts.json.
\documentclass[10pt,letterpaper]{article}
\usepackage[margin=MARGIN]{geometry}
\usepackage[T1]{fontenc}
\usepackage{lmodern,graphicx,xcolor,inconsolata,tikz,longtable,booktabs,array}
\usepackage[hidelinks]{hyperref}
\pdfinfoomitdate=1
\pdftrailerid{}
\pagestyle{empty}
\setlength{\parindent}{0pt}
\setlength{\parskip}{5pt}
\setlength{\emergencystretch}{2em}
\renewcommand{\familydefault}{\sfdefault}
\input{RELATIVEfrk-reference-colors.tex}
\hypersetup{pdftitle={Field Repair Kit: Part Reference},pdfauthor={Team Rubicon}}
\begin{document}
'''.replace('MARGIN', margin).replace('RELATIVE', relative)

    def card(self, p, offline=False):
        base = f'parts/{p["id"]}/' if offline else ''
        logo = 'parts/images/team-rubicon-logo.png' if offline else '../images/team-rubicon-logo.png'
        mode = 'offline' if offline else 'tex'
        plural = '' if p['count'] == 1 else 's'
        body = (r'\pdfbookmark[1]{' + tex(p['name'] + ' — ' + p['number']) + '}{bookmark-' + p['id'] + r'}\hypertarget{' + p['id'] + r'}{}\label{part-' + p['id'] + '}\n') if offline else ''
        body += r'''\begin{minipage}[c]{0.83\linewidth}
\begin{minipage}[c]{1.08in}\includegraphics[width=\linewidth]{LOGO}\end{minipage}\hspace{0.16in}%
\begin{minipage}[c]{2.8in}{\fontsize{8}{10}\selectfont\color{frkSecondary} FIELD REPAIR KIT --- PART REFERENCE}\end{minipage}\par
{\fontsize{21}{24}\selectfont\bfseries NAME\par}
{\fontsize{18}{21}\selectfont\ttfamily\bfseries \href{URL}{NUMBER}\par}
{\small\color{frkSecondary} Bin \textcolor{black}{\textbf{BIN}} \enspace | \enspace \textcolor{black}{\textbf{COUNT}} sparePLURAL per kit \enspace | \enspace \textcolor{frkSupervision}{\textbf{SCOPE}} supervision}
\end{minipage}\hfill
\begin{minipage}[c]{0.14\linewidth}\centering
\href{URL}{\includegraphics[width=0.82in]{BASEimages/qr.png}}\par
{\scriptsize\color{frkSecondary} Online guide}
\end{minipage}\par\smallskip\hrule\medskip
'''.replace('LOGO', logo).replace('NAME', tex(p['name'])).replace('URL', tex(self.part_url(p['id']))).replace('NUMBER', tex(p['number'])).replace('BIN', p['bin']).replace('COUNT', str(p['count'])).replace('PLURAL', plural).replace('SCOPE', p['scope']).replace('BASE', base)
        if p.get('notice'):
            body += r'{\small\color{frkSupervision}\textbf{' + tex(p['notice']) + r'}}\par\medskip' + '\n'
        apps = p['applications']
        for a in apps:
            anchor = (r'\hypertarget{' + p['id'] + '-' + a['id'] + '}{}') if offline else ''
            heading = r'{\large\bfseries\color{frkApplication} ' + tex(MODELS[a['model']] + ' — ' + application_name(a)) + r'\par}'
            facts = r'{\small\textbf{Item ' + tex(a['item']) + r' \enspace | \enspace Qty listed: ' + str(a['quantity']) + r'}}\par' + '\n'
            source = r'{\footnotesize\color{frkSecondary} ' + tex(self.source(a)) + r'}\par' + '\n'
            description = self.render_text(a['text'], mode) + '\\par\n'
            figure = base + 'images/' + a['id'] + '.png'
            if len(apps) == 1:
                body += anchor + heading + description + facts + r'\begin{center}\includegraphics[width=5.3in,height=5.5in,keepaspectratio]{' + figure + r'}\end{center}' + '\n' + source
            else:
                height = '2.05in' if len(apps) == 3 else '3.0in'
                body += anchor + r'\noindent\begin{minipage}[c]{0.40\linewidth}\raggedright' + heading + r'{\small ' + description + r'}\smallskip' + '\n' + facts + source + r'\end{minipage}\hfill\begin{minipage}[c]{0.57\linewidth}\centering\includegraphics[width=\linewidth,height=' + height + ',keepaspectratio]{' + figure + r'}\end{minipage}\par\medskip' + '\n'
        body += r'\vfill\hrule\smallskip{\footnotesize\color{frkSecondary} Drawing \textcopyright{} ANDREAS STIHL AG \& Co. KG. Not to scale.\hfill ' + self.data['revision']
        if offline:
            body += r'\enspace | \hyperlink{index}{Index}\enspace | \thepage'
        return body + '}\n'

    def label(self, p, x, y, base=''):
        name = tex(p['name'])
        main = p['applications'][0]['label']
        others = list(dict.fromkeys(a['label'] for a in p['applications'][1:] if a['label'] != main))
        if p['id'] == '90223711020':
            others = ['462 AV; 261 cylinder']
        if p.get('status') == 'unconfirmed':
            main, others = 'Fit unconfirmed', ['135 / 261 / 462 listed']
        # A complete name block spans the label above the QR. Long names wrap as one block.
        name_size = 9 if len(p['name']) <= 29 else 8
        main_size = 8.4 if len(main) < 24 else 7.7
        secondary = 'Also: ' + '; '.join(others) if others else ''
        if p.get('status') == 'unconfirmed':
            secondary = '; '.join(others)
        return r'''\begin{scope}[shift={(X,Y)}]
\node[anchor=north west,inner sep=0,text width=181bp,font=\sffamily\bfseries\fontsize{NS}{10}\selectfont] at (4,3) {NAME};
\node[anchor=west,inner sep=0,font=\ttfamily\bfseries\fontsize{11.5}{12}\selectfont] at (4,26) {NUMBER};
\node[anchor=west,inner sep=0,text=COLOR,font=\sffamily\bfseries\fontsize{MS}{9}\selectfont] at (4,39) {MAIN};
\node[anchor=north west,inner sep=0,text width=124bp,text=frkSecondary,font=\sffamily\fontsize{7}{8}\selectfont] at (4,46) {SECONDARY};
\node[anchor=west,inner sep=0,text=frkSecondary,font=\sffamily\fontsize{8}{9}\selectfont] at (4,65) {Bin~\textcolor{black}{\textbf{BIN}}\quad Qty~\textcolor{black}{\textbf{COUNT}}\quad \textcolor{frkSupervision}{\textbf{SCOPE}}};
\node[anchor=north west,inner sep=0] at (131,15) {\includegraphics[width=54bp,height=54bp]{QR}};
\end{scope}
'''.replace('X,Y', f'{x},{y}').replace('NS', str(name_size)).replace('NAME', name).replace('NUMBER', tex(p['number'])).replace('COLOR', 'frkSupervision' if p.get('status') == 'unconfirmed' else 'frkApplication').replace('MS', str(main_size)).replace('MAIN', tex(main)).replace('SECONDARY', tex(secondary)).replace('BIN', p['bin']).replace('COUNT', str(p['count'])).replace('SCOPE', p['scope']).replace('QR', base + 'images/qr.png')

    def label_sheet(self, parts, individual=False):
        result = self.preamble('0in', '../../' if individual else '')
        for page in range(math.ceil(len(parts)/30)):
            if page:
                result += '\\newpage\n'
            result += r'\null\begin{tikzpicture}[remember picture,overlay,x=1bp,y=-1bp]\begin{scope}[shift={(current page.north west)}]' + '\n'
            for i, p in enumerate(parts[page*30:(page+1)*30]):
                result += self.label(p, 13.5 + (i % 3)*198, 36+(i//3)*72, '' if individual else f'parts/{p["id"]}/')
            result += '\\end{scope}\\end{tikzpicture}\n'
        return result + '\\end{document}\n'

    def markdown(self, p):
        text = f'# {p["name"]}\n\n**{p["number"]}**\n\nBin **{p["bin"]}** · **{p["count"]}** per kit · **{p["scope"]}** supervision\n\n'
        text += f'[Part page]({self.part_url(p["id"])}) · [Field card PDF](field-card.pdf?raw=1) · [Avery label PDF](bag-label.pdf?raw=1) · [Offline reference](../../frk-part-reference.pdf?raw=1)\n\n'
        if p.get('notice'):
            text += f'> **{p["notice"]}**\n\n'
        for a in p['applications']:
            text += ('<a id="ms-462--starter--fan-housing"></a>\n' if p['id'] == '90223711020' and a['id'] == 'ms462-starter' else '')
            text += f'<a id="{a["id"]}"></a>\n\n## {MODELS[a["model"]]} — {application_name(a)}\n\n'
            text += self.render_text(a['text'], 'md') + '\n\n'
            text += f'**Item {a["item"]} · Qty listed: {a["quantity"]}**\n\n'
            path = f'images/{a["id"]}.png'
            alt = f'{MODELS[a["model"]]} {a["name"]}, item {a["item"]} highlighted'
            width = round(min(440, 300*a['crop'][2]/a['crop'][3]))
            text += f'<a href="{path}"><img src="{path}" alt="{html.escape(alt)}" width="{width}"></a>\n\n'
            text += self.source(a) + '\n\n'
        return text + 'Drawings © ANDREAS STIHL AG & Co. KG. Not to scale.\n'

    def offline_book(self):
        parts = sorted(self.parts.values(), key=lambda p: (p['name'].casefold(), p['number']))
        out = self.preamble(book=True)
        out += r'\pdfbookmark[0]{Part index}{book-index}\hypertarget{index}{}\includegraphics[width=1.08in]{parts/images/team-rubicon-logo.png}\hfill{\small '+self.data['revision']+r'}\par{\LARGE\bfseries Field Repair Kit: Part Reference}\par'
        out += r'{\color{frkSecondary} HT 135 \enspace / \enspace MS 261 \enspace / \enspace MS 462 \hfill '+str(len(parts))+r' parts}\par\medskip'
        out += r'\renewcommand{\arraystretch}{1.28}\begin{longtable}{@{}p{3.52in}p{1.68in}p{0.65in}r@{}}\toprule\textbf{Part} & \textbf{Part number} & \textbf{Bin} & \textbf{Page}\\\midrule\endfirsthead\multicolumn{4}{@{}l}{\large\bfseries Part index (continued)}\\\toprule\textbf{Part} & \textbf{Part number} & \textbf{Bin} & \textbf{Page}\\\midrule\endhead'
        for p in parts:
            key = p['id']
            name = p['name'] + (' (fit unconfirmed)' if p.get('status') == 'unconfirmed' else '')
            out += r'\hyperlink{' + key + '}{'+tex(name)+r'} & \hyperlink{'+key+r'}{\texttt{'+p['number']+'}} & '+p['bin']+r' & \pageref{part-'+key+r'}\\'+'\n'
        out += r'\bottomrule\end{longtable}\clearpage' + '\n'
        for i, p in enumerate(parts):
            if i:
                out += '\\clearpage\n'
            out += self.card(p, offline=True)
        return out + '\\end{document}\n'

    def inventory(self):
        out = self.preamble('0.4in')
        out += r'{\Large\bfseries Field Repair Kit — Inventory}\hfill '+self.data['revision']+r'\par{\small Kit: \rule{1.1in}{0.3pt}\hfill Checked by: \rule{1.4in}{0.3pt}\hfill Date: \rule{1in}{0.3pt}}\par'
        out += r'\small\renewcommand{\arraystretch}{1.22}\setlength{\tabcolsep}{4pt}\begin{longtable}{@{}lp{2.78in}p{1.23in}lp{0.70in}rr@{}}\toprule\textbf{Bin} & \textbf{Part} & \textbf{Part number} & \textbf{Level} & \textbf{Saws} & \textbf{Req.} & \textbf{Qty}\\\midrule\endfirsthead\multicolumn{7}{@{}l}{\large\bfseries Field Repair Kit — Inventory (continued)}\\\toprule\textbf{Bin} & \textbf{Part} & \textbf{Part number} & \textbf{Level} & \textbf{Saws} & \textbf{Req.} & \textbf{Qty}\\\midrule\endhead'
        for p in self.parts.values():
            out += '\n'+p['bin']+' & '+tex(p['name'])+r' & \href{'+tex(self.part_url(p['id']))+r'}{\texttt{'+p['number']+r'}} & \textcolor{frkSupervision}{'+p['scope']+r'} & \textcolor{frkApplication}{'+', '.join(p['models'])+'} & '+str(p['count'])+r' & \rule{0.23in}{0.3pt}\\'+'\n'
        return out + r'\bottomrule\end{longtable}\end{document}'+'\n'

    def bins(self):
        result = {'A1': 'unassigned'}
        for p in self.parts.values():
            result.setdefault(p['bin'], p['category'])
        # The open area holds the TSV category plus the brake bands in that bin.
        for bin_id in ('O1', 'O2'):
            if any(p['bin'] == bin_id and p['name'] == 'Brake band' for p in self.parts.values()):
                result[bin_id] += ', brake bands'
        return result

    def bin_labels(self):
        labels = [(key, name) for key, name in self.bins().items() for _ in range(self.data['kit_count'])]
        out = self.preamble('0in')
        for page in range(math.ceil(len(labels)/30)):
            if page:
                out += '\\newpage\n'
            out += r'\null\begin{tikzpicture}[remember picture,overlay,x=1bp,y=-1bp]\begin{scope}[shift={(current page.north west)}]'
            for i, (key, name) in enumerate(labels[page*30:(page+1)*30]):
                x, y = 13.5+(i%3)*198, 36+(i//3)*72
                out += r'\node[anchor=center,inner sep=0,font=\ttfamily\bfseries\fontsize{25}{28}\selectfont] at ('+str(x+94.5)+','+str(y+22)+') {'+key+'};\n'
                out += r'\node[anchor=north,align=center,text width=175bp,inner sep=0,text=frkApplication,font=\sffamily\fontsize{10}{11}\selectfont] at ('+str(x+94.5)+','+str(y+40)+') {'+tex(name)+'};\n'
            out += '\\end{scope}\\end{tikzpicture}\n'
        return out + '\\end{document}\n'

    def sources(self):
        write('frk-reference-colors.tex', '% Shared color roles, generated by scripts/build.py.\n'+''.join(r'\definecolor{frk'+key.title()+r'}{HTML}{'+value+'}\n' for key, value in COLORS.items()))
        for p in self.parts.values():
            base = f'parts/{p["id"]}/'
            write(base+'README.md', self.markdown(p))
            write(base+'field-card.tex', self.preamble(relative='../../')+self.card(p)+'\\end{document}\n')
            write(base+'bag-label.tex', self.label_sheet([p], individual=True))
        write('frk-parts-labels-avery.tex', self.label_sheet(list(self.parts.values())))
        write('frk-bin-labels-avery.tex', self.bin_labels())
        write('frk-parts-inventory.tex', self.inventory())
        write('frk-part-reference.tex', self.offline_book())
        write('frk-bin-contents.tex', '% Generated bin names from frk_items.tsv.\n'+''.join(r'\expandafter\def\csname frkbin'+k+r'\endcsname{'+tex(v)+'}\n' for k, v in self.bins().items()))
        index = '# Parts\n\n[Online reference]('+self.url+'/) · [Offline reference PDF](../frk-part-reference.pdf?raw=1)\n\n| Part | Part number | Models | Bin |\n| :--- | :--- | :--- | :--- |\n'
        for p in self.parts.values():
            name = p['name'] + (' — fit unconfirmed' if p.get('status') else '')
            index += f'| [{name}]({p["id"]}/README.md) | {p["number"]} | {", ".join(p["models"])} | {p["bin"]} |\n'
        write('parts/README.md', index)

    def html_page(self, title, body, depth=0):
        prefix = '../'*depth
        return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#{COLORS['application']}"><title>{html.escape(title)} · Field Repair Kit</title>
<link rel="stylesheet" href="{prefix}assets/site.css"></head><body>
<header class="masthead"><a href="{prefix}index.html"><img src="{prefix}assets/team-rubicon-logo.png" alt="Team Rubicon"><span>Field Repair Kit<span class="sub">Part Reference</span></span></a></header>
<main>{body}</main><footer>Drawings © ANDREAS STIHL AG &amp; Co. KG. Not to scale. <span>{self.data['revision']}</span></footer>
</body></html>'''

    def site(self):
        write('docs/.nojekyll', '')
        css = (ROOT/'reference/site.css').read_text()
        for role, value in COLORS.items():
            css = css.replace('@'+role.upper()+'@', '#'+value)
        write('docs/assets/site.css', css)
        copy('reference/search.js', 'docs/assets/search.js')
        copy('parts/images/team-rubicon-logo.png', 'docs/assets/team-rubicon-logo.png')
        for name in ('frk-part-reference', 'frk-parts-labels-avery', 'frk-bin-labels-avery', 'frk-parts-inventory', 'frk-parts-boxmap'):
            copy(name+'.pdf', 'docs/downloads/'+name+'.pdf')
        index = '<h1>Parts</h1><p class="intro">HT 135 / MS 261 / MS 462</p><nav class="downloads" aria-label="Documents"><a href="downloads/frk-part-reference.pdf" download>Offline reference PDF</a><a href="downloads/frk-parts-labels-avery.pdf">Part labels</a><a href="downloads/frk-parts-inventory.pdf">Inventory</a><a href="downloads/frk-parts-boxmap.pdf">Box map</a><a href="downloads/frk-bin-labels-avery.pdf">Bin labels</a></nav>'
        index += '<div class="filters" hidden><label>Find a part<input id="search" type="search" placeholder="Name, number, application or bin" autocomplete="off"></label><label>Model<select id="model"><option value="">All models</option>'+''.join(f'<option value="{k}">{v}</option>' for k,v in MODELS.items())+'</select></label><label>Bin<select id="bin"><option value="">All bins</option>'+''.join(f'<option>{b}</option>' for b in self.bins() if b!='A1')+'</select></label></div><p id="results" role="status">'+str(len(self.parts))+' parts</p><ul class="parts">'
        for p in self.parts.values():
            pid = p['id']
            applications = '; '.join(a['label'] for a in p['applications'])
            searchable = ' '.join([p['name'], p['number'], pid, p['bin'], p['category'], applications])
            status = ' · Fit unconfirmed' if p.get('status') else ''
            index += f'<li data-search="{html.escape(searchable.lower(), quote=True)}" data-models="{",".join(p["models"])}" data-bin="{p["bin"]}"><a href="parts/{pid}/"><span class="part-name">{html.escape(p["name"])}</span><span class="part-number">{p["number"]}</span><span class="location">{html.escape(applications)}{status}</span><span class="bin">{p["bin"]} · Qty {p["count"]}</span></a></li>'
            body = f'<nav class="crumb"><a href="../../index.html">All parts</a><span>Bin {p["bin"]}</span></nav><h1>{html.escape(p["name"])}</h1><p class="number">{p["number"]}</p><div class="metadata"><span>Bin <strong>{p["bin"]}</strong></span><span><strong>{p["count"]}</strong> per kit</span><span class="supervision"><strong>{p["scope"]}</strong> supervision</span></div><nav class="downloads" aria-label="Downloads"><a href="field-card.pdf" download>Field card PDF</a><a href="bag-label.pdf" download>Avery label PDF</a><a href="../../downloads/frk-part-reference.pdf" download>Offline reference PDF</a></nav>'
            if p.get('notice'):
                body += '<p class="notice">'+html.escape(p['notice'])+'</p>'
            if len(p['applications']) > 1:
                body += '<nav class="applications" aria-label="Applications">'+''.join(f'<a href="#{a["id"]}">{MODELS[a["model"]]} — {html.escape(application_name(a))}</a>' for a in p['applications'])+'</nav>'
            for a in p['applications']:
                figure = a['id']+'.png'
                alt = f'{MODELS[a["model"]]} {a["name"]}, item {a["item"]} highlighted'
                body += f'<section class="application" id="{a["id"]}"><div><h2>{MODELS[a["model"]]} — {html.escape(application_name(a))}</h2><p>{self.render_text(a["text"], "html")}</p><p class="facts">Item {a["item"]} <span>Qty listed: {a["quantity"]}</span></p></div><a class="diagram" href="{figure}" aria-label="Open full-size diagram: {html.escape(alt)}"><img src="{figure}" alt="{html.escape(alt)}" loading="lazy"></a><p class="source">{html.escape(self.source(a))}</p></section>'
                copy(f'parts/{pid}/images/{figure}', f'docs/parts/{pid}/{figure}')
            for file in ('field-card.pdf', 'bag-label.pdf'):
                copy(f'parts/{pid}/{file}', f'docs/parts/{pid}/{file}')
            write(f'docs/parts/{pid}/index.html', self.html_page(p['name'], body, 2))
        index += '</ul><p id="empty" hidden>No matching parts.</p><script src="assets/search.js" defer></script>'
        write('docs/index.html', self.html_page('Parts', index))


def compile_pdf(source, passes=1, executable='pdflatex'):
    source = ROOT / source
    output = ROOT / '.build' / source.relative_to(ROOT).with_suffix('')
    output.mkdir(parents=True, exist_ok=True)
    # Avoid changing committed PDFs when inputs and compiler version have not changed.
    dependencies = [source, ROOT/'frk-reference-colors.tex']
    for entry in re.findall(r'\\(?:includegraphics(?:\[[^]]*\])?|input)\{([^}]+)\}', source.read_text()):
        dependency = source.parent / entry
        if dependency.is_file():
            dependencies.append(dependency)
    compiler = Path(shutil.which(executable) or executable).resolve()
    build_settings = f'{passes}:{compiler}:{compiler.stat().st_mtime_ns}'.encode()
    fingerprint = hashlib.sha256(build_settings+b''.join(p.read_bytes() for p in dependencies)).hexdigest()
    stamp = output/'fingerprint'
    if source.with_suffix('.pdf').exists() and stamp.exists() and stamp.read_text() == fingerprint:
        return
    for _ in range(passes):
        process = subprocess.run([executable, '-halt-on-error', '-interaction=nonstopmode',
                                  f'-output-directory={output}', source.name],
                                 cwd=source.parent, capture_output=True, text=True)
        if process.returncode:
            raise RuntimeError(f'{source.relative_to(ROOT)} failed:\n{process.stdout[-5000:]}')
    log = (output/source.with_suffix('.log').name).read_text(errors='replace')
    if 'Overfull \\hbox' in log or 'Overfull \\vbox' in log:
        raise RuntimeError(f'Layout overflow in {source.relative_to(ROOT)}; see {output}')
    copy(output/source.with_suffix('.pdf').name, source.with_suffix('.pdf'))
    stamp.write_text(fingerprint)


class PageLinks(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.ids, self.links = set(), []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.add(attrs['id'])
        for key in ('src', 'href'):
            if key in attrs:
                self.links.append(attrs[key])


def check_site(ref):
    pages = {path.resolve(): PageLinks(path.read_text()) for path in (ROOT/'docs').rglob('*.html')}
    if len(pages) != len(ref.parts)+1:
        raise ValueError('Website page count differs from the inventory')
    count = 0
    for path, page in pages.items():
        for link in page.links:
            url = urlsplit(link)
            if url.scheme or url.netloc:
                raise ValueError(f'Unexpected external dependency in site: {link}')
            target = (path.parent/unquote(url.path)).resolve() if url.path else path
            if target.is_dir():
                target /= 'index.html'
            if not target.is_file():
                raise ValueError(f'Broken site link: {path} → {link}')
            if url.fragment and (target not in pages or url.fragment not in pages[target].ids):
                raise ValueError(f'Broken site anchor: {path} → {link}')
            count += 1
    print(f'Checked {len(pages)} web pages and {count} local links/assets.')


def check_pdfs(ref):
    def check_label_position(page, number, column=0, row=0):
        positions = []
        def capture(text, cm, tm, font, size):
            if identifier(text.strip()) == identifier(number):
                positions.append((cm[0]*tm[4]+cm[2]*tm[5]+cm[4],
                                  cm[1]*tm[4]+cm[3]*tm[5]+cm[5]))
        page.extract_text(visitor_text=capture)
        left, top = 13.5+column*198, 792-36-row*72
        if len(positions) != 1 or not (left <= positions[0][0] <= left+125 and top-72 <= positions[0][1] <= top):
            raise ValueError(f'Part number outside its Avery label: {number}, {positions}')

    for p in ref.parts.values():
        for file in ('field-card', 'bag-label'):
            path = ROOT/f'parts/{p["id"]}/{file}.pdf'
            pdf = PdfReader(path)
            if len(pdf.pages) != 1:
                raise ValueError(f'Expected a single page: {path}')
            text = pdf.pages[0].extract_text()
            if identifier(p['number']) not in identifier(text):
                raise ValueError(f'Part number missing from {path}')
            if identifier(p['name']) not in identifier(text):
                raise ValueError(f'Part name missing from {path}')
            quantity = rf'(?<!\d){p["count"]}\s*spare' if file == 'field-card' else rf'Qty\s*{p["count"]}(?!\d)'
            scope = rf'\b{p["scope"]}\s*supervision' if file == 'field-card' else rf'\b{p["scope"]}\b'
            if not re.search(quantity, text) or not re.search(scope, text):
                raise ValueError(f'Inventory quantity or supervision differs in {path}')
            if file == 'bag-label':
                check_label_position(pdf.pages[0], p['number'])
        card = PdfReader(ROOT/f'parts/{p["id"]}/field-card.pdf')
        uris = {str(a.get_object().get('/A', {}).get('/URI', '')) for a in card.pages[0].get('/Annots', [])}
        if ref.part_url(p['id']) not in uris:
            raise ValueError(f'Part number/QR link missing: {p["id"]}')
        for a in p['applications']:
            for key, anchor in LINK.findall(a['text']):
                if ref.part_url(key, anchor) not in uris:
                    raise ValueError(f'Related part link missing: {p["id"]} → {key}')
    labels = PdfReader(ROOT/'frk-parts-labels-avery.pdf')
    if len(labels.pages) != math.ceil(len(ref.parts)/30):
        raise ValueError('Incorrect Avery sheet count')
    for i, p in enumerate(ref.parts.values()):
        check_label_position(labels.pages[i//30], p['number'], i%3, (i%30)//3)
    book = PdfReader(ROOT/'frk-part-reference.pdf')
    destinations = book.named_destinations
    for p in ref.parts.values():
        if p['id'] not in destinations:
            raise ValueError(f'Offline destination missing: {p["id"]}')
    internal_links = 0
    for page in book.pages:
        for annotation in page.get('/Annots', []):
            action = annotation.get_object().get('/A', {})
            if action.get('/S') == '/GoTo':
                target = action.get('/D')
                if isinstance(target, str) and target not in destinations:
                    raise ValueError(f'Broken offline destination: {target}')
                internal_links += 1
    if internal_links < len(ref.parts)*2:
        raise ValueError('Offline index/related links are missing')
    print(f'Checked {len(ref.parts)} one-page cards, {len(ref.parts)} labels, {len(book.pages)} offline pages and {internal_links} internal PDF links.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', nargs='?', default='all', choices=['all', 'generate', 'check'])
    parser.add_argument('--pdflatex', default=os.environ.get('PDFLATEX', 'pdflatex'))
    args = parser.parse_args()
    ref = Reference()
    if args.target == 'check':
        check_pdfs(ref)
        check_site(ref)
        return
    ref.assets()
    ref.sources()
    if args.target == 'generate':
        return
    jobs = [(f'parts/{p["id"]}/{name}.tex', 2 if name == 'bag-label' else 1) for p in ref.parts.values() for name in ('field-card', 'bag-label')]
    jobs += [('frk-part-reference.tex', 3), ('frk-parts-labels-avery.tex', 2), ('frk-bin-labels-avery.tex', 2), ('frk-parts-inventory.tex', 2), ('frk-parts-boxmap.tex', 1)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda job: compile_pdf(*job, executable=args.pdflatex), jobs))
    ref.site()
    check_pdfs(ref)
    check_site(ref)
    print(f'Built {args.target}.')


if __name__ == '__main__':
    main()

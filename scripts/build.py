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
from urllib.parse import unquote, urlencode, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw
import qrcode
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'reference/parts.json'
LAYOUT = ROOT / 'reference/bin-layout.json'
LINK = re.compile(r'\[\[([a-z0-9]+)(?:#([a-z0-9-]+))?\]\]')
MODELS = {'135': 'HT 135', '261': 'MS 261', '462': 'MS 462'}
COLORS = {'application': '075985', 'supervision': '92400E', 'secondary': '52525B'}
CORRECTION_URL = 'https://github.com/rduncangt/frk-management/issues/new'


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


def tex_node(x, y, value, size=9, width=None, anchor='north west', color='black', bold=False, raw=False):
    options = f'anchor={anchor},inner sep=0,align=left,text={color},font=\\sffamily'
    if bold:
        options += r'\bfseries'
    options += f'\\fontsize{{{size}}}{{{size+1.5}}}\\selectfont'
    if width:
        options += f',text width={width:g}bp'
    return f'\\node[{options}] at ({x:g},{y:g}) {{{value if raw else tex(value)}}};\n'


def write(path, content):
    path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = content.encode() if isinstance(content, str) else content
    if not path.exists() or path.read_bytes() != raw:
        path.write_bytes(raw)


def copy(source, target):
    write(target, (ROOT / source).read_bytes())


class Reference:
    def __init__(self, data=DATA, inventory=ROOT / 'frk_items.tsv', layout=LAYOUT):
        self.data = json.loads(Path(data).read_text())
        self.layout = json.loads(Path(layout).read_text())
        self.bin_layout = {}
        for section in self.layout['sections']:
            for slot in section['bins']:
                key = slot['id']
                if key in self.bin_layout:
                    raise ValueError(f'Duplicate bin in layout: {key}')
                self.bin_layout[key] = dict(slot, section=section['name'])
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
        order = {key: i for i, key in enumerate(self.bin_layout)}
        self.parts = dict(sorted(self.parts.items(), key=lambda item: order[item[1]['bin']]))

    def validate(self):
        if set(self.parts) != set(self.data['parts']):
            raise ValueError('Reference and inventory part numbers differ')
        if not self.url.startswith('https://'):
            raise ValueError('QR destination must use HTTPS')
        bin_names = {}
        for p in self.parts.values():
            if p['bin'] not in self.bin_layout:
                raise ValueError(f'Unknown bin: {p["bin"]} / {p["number"]}')
            name = bin_names.setdefault(p['bin'], p['category'])
            if name != p['category']:
                raise ValueError(f'Conflicting contents names for bin {p["bin"]}')
            if self.bin_layout[p['bin']].get('reserve'):
                raise ValueError(f'Standard inventory assigned to field-extras bin: {p["number"]}')
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
        for section in self.layout['sections']:
            width, height = section['size']
            rectangles = []
            for slot in section['bins']:
                x, y, w, h = slot.get('rect', slot.get('label_rect', []))
                if min(x, y) < 0 or min(w, h) <= 0 or x+w > width or y+h > height:
                    raise ValueError(f'Bin outside box map: {slot["id"]}')
                for key, (ox, oy, ow, oh) in rectangles:
                    if x < ox+ow and ox < x+w and y < oy+oh and oy < y+h:
                        raise ValueError(f'Overlapping bin areas: {key} / {slot["id"]}')
                rectangles.append((slot['id'], (x, y, w, h)))
                if slot['id'] not in bin_names and not slot.get('name'):
                    raise ValueError(f'Bin has no contents name: {slot["id"]}')

    def part_url(self, part_id, anchor=''):
        return f'{self.url}/parts/{part_id}/' + (f'#{anchor}' if anchor else '')

    def correction_url(self, p):
        body = (f'**Part:** {p["name"]}\n'
                f'**Part number:** {p["number"]}\n'
                f'**Saw model(s):** {", ".join(MODELS[model] for model in p["models"])}\n'
                f'**Part reference:** {self.part_url(p["id"])}\n\n'
                '**What needs changing:**\n\n\n'
                '**Reference or photo (if available):**\n')
        return CORRECTION_URL + '?' + urlencode({
            'title': f'Parts correction: {p["number"]} — {p["name"]}',
            'body': body,
        })

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
        write(f'docs/parts/{p["id"]}/{a["id"]}.png', output.getvalue())

    def assets(self):
        for p in self.parts.values():
            qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
            qr.add_data(self.part_url(p['id']))
            qr.make(fit=True)
            output = io.BytesIO()
            qr.make_image(fill_color='black', back_color='white').save(output, format='PNG')
            write(f'docs/parts/{p["id"]}/qr.png', output.getvalue())
            for a in p['applications']:
                self.figure(p, a)

    def preamble(self, margin='0.45in', relative='', book=False, packages=()):
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
PACKAGES\begin{document}
'''.replace('MARGIN', margin).replace('RELATIVE', relative).replace('PACKAGES', ''.join(r'\usepackage{' + package + '}\n' for package in packages))

    def card(self, p, offline=False):
        prefix = '' if offline else '../../../'
        base = f'{prefix}docs/parts/{p["id"]}/'
        logo = prefix + 'reference/team-rubicon-logo.png'
        mode = 'offline' if offline else 'tex'
        plural = '' if p['count'] == 1 else 's'
        body = (r'\pdfbookmark[1]{' + tex(p['name'] + ' — ' + p['number']) + '}{bookmark-' + p['id'] + r'}\hypertarget{' + p['id'] + r'}{}\label{part-' + p['id'] + '}\n') if offline else ''
        body += r'''\begin{minipage}[c]{0.83\linewidth}
\begin{minipage}[c]{1.08in}\includegraphics[width=\linewidth]{LOGO}\end{minipage}\hspace{0.16in}%
\begin{minipage}[c]{2.8in}{\fontsize{8}{10}\selectfont\color{frkSecondary} FIELD REPAIR KIT --- PART REFERENCE SHEET}\end{minipage}\par
{\fontsize{21}{24}\selectfont\bfseries NAME\par}
{\fontsize{18}{21}\selectfont\ttfamily\bfseries \href{URL}{NUMBER}\par}
{\small\color{frkSecondary} Bin \textcolor{black}{\textbf{BIN}} \enspace | \enspace \textcolor{black}{\textbf{COUNT}} sparePLURAL per kit \enspace | \enspace \textcolor{frkSupervision}{\textbf{SCOPE}} supervision}
\end{minipage}\hfill
\begin{minipage}[c]{0.14\linewidth}\centering
\href{URL}{\includegraphics[width=0.82in]{BASEqr.png}}\par
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
            figure = base + a['id'] + '.png'
            if len(apps) == 1:
                body += anchor + heading + description + facts + r'\begin{center}\includegraphics[width=5.3in,height=5.5in,keepaspectratio]{' + figure + r'}\end{center}' + '\n' + source
            else:
                height = '2.05in' if len(apps) == 3 else '3.0in'
                body += anchor + r'\noindent\begin{minipage}[c]{0.40\linewidth}\raggedright' + heading + r'{\small ' + description + r'}\smallskip' + '\n' + facts + source + r'\end{minipage}\hfill\begin{minipage}[c]{0.57\linewidth}\centering\includegraphics[width=\linewidth,height=' + height + ',keepaspectratio]{' + figure + r'}\end{minipage}\par\medskip' + '\n'
        body += (r'\vfill\hrule\smallskip{\footnotesize\color{frkSecondary} '
                 r'\href{' + tex(self.correction_url(p)) + r'}{\textcolor{frkApplication}{Suggest a correction}}'
                 r'\enspace (GitHub)\hfill Revision: ' + self.data['revision'])
        if offline:
            body += r'\enspace | \hyperlink{index}{Index}\enspace | \thepage'
        return body + r'\par\smallskip{\scriptsize Drawing \textcopyright{} ANDREAS STIHL AG \& Co. KG. Not to scale.}\par}' + '\n'

    def label(self, p, x, y, base=''):
        name = tex(p['name'])
        main = p['applications'][0]['label']
        others = list(dict.fromkeys(a['label'] for a in p['applications'][1:] if a['label'] != main))
        if p['id'] == '90223711020':
            others = ['462 AV; 261 cylinder']
        if p['id'] == '00001957200':
            main, others = '135 / 261 / 462 rewind starter', []
        # A complete name block spans the label above the QR. Long names wrap as one block.
        name_size = 9 if len(p['name']) <= 29 else 8
        main_size = 8.4 if len(main) < 24 else 7.7
        secondary = 'Also: ' + '; '.join(others) if others else ''
        return r'''\begin{scope}[shift={(X,Y)}]
\node[anchor=north west,inner sep=0,text width=177bp,font=\sffamily\bfseries\fontsize{NS}{10}\selectfont] at (6,6) {NAME};
\node[anchor=west,inner sep=0,font=\ttfamily\bfseries\fontsize{11.5}{12}\selectfont] at (6,26) {NUMBER};
\node[anchor=west,inner sep=0,text=frkApplication,font=\sffamily\bfseries\fontsize{MS}{9}\selectfont] at (6,39) {MAIN};
\node[anchor=north west,inner sep=0,text width=119bp,text=frkSecondary,font=\sffamily\fontsize{7}{8}\selectfont] at (6,46) {SECONDARY};
\node[anchor=west,inner sep=0,text=frkSecondary,font=\sffamily\fontsize{8}{9}\selectfont] at (6,62) {Bin~\textcolor{black}{\textbf{BIN}}\quad Qty~\textcolor{black}{\textbf{COUNT}}\quad \textcolor{frkSupervision}{\textbf{SCOPE}}};
\node[anchor=north west,inner sep=0] at (131,14) {\includegraphics[width=52bp,height=52bp]{QR}};
\end{scope}
'''.replace('X,Y', f'{x},{y}').replace('NS', str(name_size)).replace('NAME', name).replace('NUMBER', tex(p['number'])).replace('MS', str(main_size)).replace('MAIN', tex(main)).replace('SECONDARY', tex(secondary)).replace('BIN', p['bin']).replace('COUNT', str(p['count'])).replace('SCOPE', p['scope']).replace('QR', base + 'qr.png')

    def label_preamble(self, filename):
        source = ROOT/filename
        preamble = source.read_text().split(r'\begin{document}', 1)[0] if source.exists() else ''
        settings = re.findall(r'(?m)^[ \t]*\\labelboundaries(true|false)\b', preamble)
        setting = settings[-1] if settings else 'true'
        options = r'''% Avery label outlines: use \labelboundariesfalse to hide them.
\newif\iflabelboundaries
\labelboundariesSETTING
'''.replace('SETTING', setting)
        return self.preamble('0in').replace(r'\begin{document}', options + r'\begin{document}')

    @staticmethod
    def label_outlines():
        return r'''\iflabelboundaries
\foreach \x in {13.5,211.5,409.5}{%
  \foreach \y in {36,108,...,684}{%
    \draw[black!20,line width=0.3bp] (\x,\y) rectangle ++(189,72);
  }
}
\fi
'''

    def label_sheet(self, parts, individual=False):
        prefix = '../../../' if individual else ''
        result = self.preamble('0in', prefix) if individual else self.label_preamble('frk-parts-labels-avery.tex')
        for page in range(math.ceil(len(parts)/30)):
            if page:
                result += '\\newpage\n'
            result += r'\null\begin{tikzpicture}[remember picture,overlay,x=1bp,y=-1bp]\begin{scope}[shift={(current page.north west)}]' + '\n'
            if not individual:
                result += self.label_outlines()
            for i, p in enumerate(parts[page*30:(page+1)*30]):
                result += self.label(p, 13.5 + (i % 3)*198, 36+(i//3)*72, f'{prefix}docs/parts/{p["id"]}/')
            result += '\\end{scope}\\end{tikzpicture}\n'
        return result + '\\end{document}\n'

    def markdown(self, p):
        text = f'# {p["name"]}\n\n**{p["number"]}**\n\nBin **{p["bin"]}** · **{p["count"]}** per kit · **{p["scope"]}** supervision\n\n'
        text += f'[Part page]({self.part_url(p["id"])}) · [Part reference sheet PDF](field-card.pdf?raw=1) · [Avery label PDF](bag-label.pdf?raw=1) · [Offline reference](../../downloads/frk-part-reference.pdf?raw=1)\n\n'
        if p.get('notice'):
            text += f'> **{p["notice"]}**\n\n'
        for a in p['applications']:
            text += ('<a id="ms-462--starter--fan-housing"></a>\n' if p['id'] == '90223711020' and a['id'] == 'ms462-starter' else '')
            text += f'<a id="{a["id"]}"></a>\n\n## {MODELS[a["model"]]} — {application_name(a)}\n\n'
            text += self.render_text(a['text'], 'md') + '\n\n'
            text += f'**Item {a["item"]} · Qty listed: {a["quantity"]}**\n\n'
            path = f'{a["id"]}.png'
            alt = f'{MODELS[a["model"]]} {a["name"]}, item {a["item"]} highlighted'
            width = round(min(440, 300*a['crop'][2]/a['crop'][3]))
            text += f'<a href="{path}"><img src="{path}" alt="{html.escape(alt)}" width="{width}"></a>\n\n'
            text += self.source(a) + '\n\n'
        return text + f'[Suggest a correction]({self.correction_url(p)}) · GitHub sign-in required.\n\nDrawings © ANDREAS STIHL AG & Co. KG. Not to scale.\n'

    def offline_book(self):
        parts = sorted(self.parts.values(), key=lambda p: (p['name'].casefold(), p['number']))
        out = self.preamble(book=True)
        out += r'\pdfbookmark[0]{Part index}{book-index}\hypertarget{index}{}\includegraphics[width=1.08in]{reference/team-rubicon-logo.png}\hfill{\small '+self.data['revision']+r'}\par{\LARGE\bfseries Field Repair Kit: Part Reference}\par'
        out += r'{\color{frkSecondary} HT 135 \enspace / \enspace MS 261 \enspace / \enspace MS 462 \hfill '+str(len(parts))+r' parts}\par\medskip'
        out += r'\renewcommand{\arraystretch}{1.28}\begin{longtable}{@{}p{3.52in}p{1.68in}p{0.65in}r@{}}\toprule\textbf{Part} & \textbf{Part number} & \textbf{Bin} & \textbf{Page}\\\midrule\endfirsthead\multicolumn{4}{@{}l}{\large\bfseries Part index (continued)}\\\toprule\textbf{Part} & \textbf{Part number} & \textbf{Bin} & \textbf{Page}\\\midrule\endhead'
        for p in parts:
            key = p['id']
            name = p['name']
            out += r'\hyperlink{' + key + '}{'+tex(name)+r'} & \hyperlink{'+key+r'}{\texttt{'+p['number']+'}} & '+p['bin']+r' & \pageref{part-'+key+r'}\\'+'\n'
        out += r'\bottomrule\end{longtable}\clearpage' + '\n'
        for i, p in enumerate(parts):
            if i:
                out += '\\clearpage\n'
            out += self.card(p, offline=True)
        return out + '\\end{document}\n'

    def inventory(self):
        out = r'\PassOptionsToPackage{table}{xcolor}' + '\n' + self.preamble('0.4in,top=0.85in,bottom=0.70in,headheight=0.50in,headsep=0.08in,footskip=0.30in', packages=('fancyhdr',))
        out += r'''\hypersetup{pdftitle={Field Repair Kit: Inventory}}
\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0.3pt}
\renewcommand{\footrulewidth}{0.3pt}
\fancyhead[L]{%
\begin{minipage}[c]{1.08in}\includegraphics[width=\linewidth]{reference/team-rubicon-logo.png}\end{minipage}\hspace{0.16in}%
\begin{minipage}[c]{\dimexpr\linewidth-1.24in\relax}\setlength{\parskip}{0pt}%
{\fontsize{8}{10}\selectfont\color{frkSecondary} FIELD REPAIR KIT\par}%
{\Large\bfseries Inventory\par}%
\end{minipage}}
\fancyfoot[L]{\footnotesize\color{frkSecondary}Document revision: REVISION}
\fancyfoot[R]{\footnotesize\color{frkSecondary}Page \thepage{} of \pageref*{inventory-last-page}}
'''.replace('REVISION', tex(self.data['revision']))
        out += r'{\small Kit: \rule{1.1in}{0.3pt}\hfill Checked by: \rule{1.4in}{0.3pt}\hfill Date checked: \rule{1in}{0.3pt}}\par'
        out += r'\small\renewcommand{\arraystretch}{1.9}\setlength{\tabcolsep}{4pt}\begin{longtable}{@{}l>{\raggedright\arraybackslash}p{2.58in}p{1.23in}llrr@{}}\toprule\textbf{Bin} & \textbf{Part} & \textbf{Part number} & \textbf{Level} & \textbf{Saws} & \textbf{Req.} & \textbf{Qty}\\\midrule\endfirsthead\toprule\textbf{Bin} & \textbf{Part} & \textbf{Part number} & \textbf{Level} & \textbf{Saws} & \textbf{Req.} & \textbf{Qty}\\\midrule\endhead'
        for i, p in enumerate(self.parts.values()):
            shade = r'\rowcolor{black!6}' if i % 2 else ''
            last_page = r'\label{inventory-last-page}' if i == len(self.parts)-1 else ''
            out += '\n'+shade+p['bin']+' & '+tex(p['name'])+r' & \href{'+tex(self.part_url(p['id']))+r'}{\texttt{'+p['number']+r'}} & \textcolor{frkSupervision}{'+p['scope']+r'} & \textcolor{frkApplication}{\mbox{'+', '.join(p['models'])+'}} & '+str(p['count'])+' & '+last_page+r'\rule{0.23in}{0.3pt}\\'+'\n'
        return out + r'\bottomrule\end{longtable}\end{document}'+'\n'

    def bins(self):
        names = {p['bin']: p['category'] for p in self.parts.values()}
        return {key: names.get(key, slot.get('name')) for key, slot in self.bin_layout.items()}

    def bin_labels(self):
        names = self.bins()
        labels = []
        for section in self.layout['sections']:
            # Start each level on a fresh row of the Avery sheet.
            labels.extend([None] * (-len(labels) % 3))
            labels.extend(slot['id'] for slot in section['bins'])
        out = self.label_preamble('frk-bin-labels-avery.tex')
        out += r'\hypersetup{pdftitle={Field Repair Kit: Bin labels}}' + '\n'
        for page in range(math.ceil(len(labels)/30)):
            if page:
                out += '\\newpage\n'
            out += r'\null\begin{tikzpicture}[remember picture,overlay,x=1bp,y=-1bp]\begin{scope}[shift={(current page.north west)}]' + '\n'
            out += self.label_outlines()
            for i, key in enumerate(labels[page*30:(page+1)*30]):
                if key is None:
                    continue
                slot = self.bin_layout[key]
                x, y = 13.5+(i%3)*198, 36+(i//3)*72
                out += f'\\node[anchor=west,inner sep=0,font=\\ttfamily\\bfseries\\fontsize{{25}}{{27}}\\selectfont] at ({x+9},{y+25}) {{{key}}};\n'
                out += tex_node(x+9, y+48, slot['section'].upper(), 6.5, color='frkSecondary')
                out += f'\\draw[black!20,line width=0.4bp] ({x+58},{y+10}) -- ({x+58},{y+62});\n'
                color = 'frkSecondary' if slot.get('reserve') else 'frkApplication'
                out += tex_node(x+70, y+36, names[key], 11, 112, anchor='west', color=color, bold=True)
            out += '\\end{scope}\\end{tikzpicture}\n'
        return out + '\\end{document}\n'

    def box_map(self):
        out = self.preamble('0in')
        out += r'''\hypersetup{pdftitle={Field Repair Kit: Box map}}
\input{frk-bin-contents.tex}
\null\begin{tikzpicture}[remember picture,overlay,x=1bp,y=-1bp]
\begin{scope}[shift={(current page.north west)}]
'''
        for i, section in enumerate(self.layout['sections']):
            offset = i * 396
            header_y = offset + (25 if i == 0 else 15)
            out += f'\\node[anchor=north west,inner sep=0] at (36,{header_y}) {{\\includegraphics[width=1.08in]{{reference/team-rubicon-logo.png}}}};\n'
            out += tex_node(130, header_y+1, 'FIELD REPAIR KIT', 8, color='frkSecondary')
            out += tex_node(130, header_y+14, section['name'], 18, bold=True)
            width, height = section['size']
            scale = min(540/width, 276/height)
            x, y = 36, offset + (94 if i == 0 else 72)
            out += tex_node(306, y-16, 'HINGE EDGE', 7, anchor='north', color='frkSecondary')
            out += f'\\draw[black!20,line width=0.6bp] ({x},{y}) rectangle ++({width*scale:g},{height*scale:g});\n'
            for slot in section['bins']:
                bx, by, bw, bh = [n*scale for n in slot.get('rect', slot.get('label_rect'))]
                bx, by = x+bx, y+by
                name = r'\csname frkbin' + slot['id'] + r'\endcsname'
                if 'rect' in slot:
                    fill = 'white' if slot.get('reserve') else 'frkApplication!4'
                    style = 'dashed,' if slot.get('reserve') else ''
                    color = 'frkSecondary' if slot.get('reserve') else 'frkApplication'
                    out += f'\\draw[{style}draw=black!20,fill={fill},line width=0.6bp] ({bx+2:g},{by+2:g}) rectangle ++({bw-4:g},{bh-4:g});\n'
                    out += tex_node(bx+10, by+8, slot['id'], 16, bold=True)
                    out += tex_node(bx+10, by+30, name, 9, bw-20, color=color, raw=True)
                else:
                    out += tex_node(bx+18, by+16, slot['id'], 18, bold=True)
                    out += tex_node(bx+18, by+44, name, 12, bw-36, color='frkApplication', raw=True)
                    out += tex_node(bx+18, by+bh-30, 'OPEN AREA', 7, color='frkSecondary')
            out += tex_node(306, y+height*scale+8, 'FRONT / LATCHES', 7, anchor='north', color='frkSecondary')
            out += tex_node(36, offset+378, 'Not to scale', 6.5, color='frkSecondary')
            out += tex_node(576, offset+378, 'Revision: '+self.data['revision'], 6.5, anchor='north east', color='frkSecondary')
        out += r'\draw[black!20,dashed,line width=0.4bp] (24,396) -- (588,396);' + '\n'
        return out + '\\end{scope}\\end{tikzpicture}\n\\end{document}\n'

    def sources(self):
        write('frk-reference-colors.tex', '% Shared color roles, generated by scripts/build.py.\n'+''.join(r'\definecolor{frk'+key.title()+r'}{HTML}{'+value+'}\n' for key, value in COLORS.items()))
        for p in self.parts.values():
            base = f'.build/parts/{p["id"]}/'
            write(f'docs/parts/{p["id"]}/README.md', self.markdown(p))
            title = tex(f'{p["name"]} — {p["number"]} | Part reference sheet')
            write(base+'field-card.tex', self.preamble(relative='../../../')+r'\hypersetup{pdftitle={'+title+'}}\n'+self.card(p)+'\\end{document}\n')
            write(base+'bag-label.tex', self.label_sheet([p], individual=True))
        write('frk-parts-labels-avery.tex', self.label_sheet(list(self.parts.values())))
        write('frk-bin-labels-avery.tex', self.bin_labels())
        write('frk-parts-inventory.tex', self.inventory())
        write('frk-part-reference.tex', self.offline_book())
        write('frk-bin-contents.tex', '% Generated bin names from frk_items.tsv.\n'+''.join(r'\expandafter\def\csname frkbin'+k+r'\endcsname{'+tex(v)+'}\n' for k, v in self.bins().items()))
        write('frk-parts-boxmap.tex', self.box_map())
        index = '# Parts\n\n[Online reference]('+self.url+'/) · [Offline reference PDF](../downloads/frk-part-reference.pdf?raw=1)\n\n| Part | Part number | Models | Bin |\n| :--- | :--- | :--- | :--- |\n'
        for p in self.parts.values():
            name = p['name']
            index += f'| [{name}]({p["id"]}/README.md) | {p["number"]} | {", ".join(p["models"])} | {p["bin"]} |\n'
        write('docs/parts/README.md', index)

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
        copy('reference/team-rubicon-logo.png', 'docs/assets/team-rubicon-logo.png')
        for name in ('frk-part-reference', 'frk-parts-labels-avery', 'frk-bin-labels-avery', 'frk-parts-inventory', 'frk-parts-boxmap'):
            copy(name+'.pdf', 'docs/downloads/'+name+'.pdf')
        index = '<h1>Parts</h1><p class="intro">HT 135 / MS 261 / MS 462</p><nav class="downloads" aria-label="Documents"><a href="downloads/frk-part-reference.pdf" download>Offline reference PDF</a><a href="downloads/frk-parts-labels-avery.pdf">Part labels</a><a href="downloads/frk-parts-inventory.pdf">Inventory</a><a href="downloads/frk-parts-boxmap.pdf">Box map</a><a href="downloads/frk-bin-labels-avery.pdf">Bin labels</a></nav>'
        occupied = {p['bin'] for p in self.parts.values()}
        bin_options = ''.join('<optgroup label="'+html.escape(section['name'], quote=True)+'">'+''.join(f'<option>{slot["id"]}</option>' for slot in section['bins'] if slot['id'] in occupied)+'</optgroup>' for section in self.layout['sections'])
        index += '<div class="filters" hidden><label>Find a part<input id="search" type="search" placeholder="Name, number, application or bin" autocomplete="off"></label><label>Model<select id="model"><option value="">All models</option>'+''.join(f'<option value="{k}">{v}</option>' for k,v in MODELS.items())+'</select></label><label>Bin<select id="bin"><option value="">All bins</option>'+bin_options+'</select></label></div><p id="results" role="status">'+str(len(self.parts))+' parts</p><ul class="parts">'
        for p in self.parts.values():
            pid = p['id']
            applications = '; '.join(a['label'] for a in p['applications'])
            searchable = ' '.join([p['name'], p['number'], pid, p['bin'], p['category'], applications])
            index += f'<li data-search="{html.escape(searchable.lower(), quote=True)}" data-models="{",".join(p["models"])}" data-bin="{p["bin"]}"><a href="parts/{pid}/"><span class="part-name">{html.escape(p["name"])}</span><span class="part-number">{p["number"]}</span><span class="location">{html.escape(applications)}</span><span class="part-bin"><span class="detail-label">Bin</span><span>{p["bin"]}</span></span><span class="part-quantity"><span class="detail-label">Per kit</span><span>{p["count"]}</span></span></a></li>'
            body = f'<nav class="crumb"><a href="../../index.html">All parts</a><span>Bin {p["bin"]}</span></nav><h1>{html.escape(p["name"])}</h1><p class="number">{p["number"]}</p><div class="metadata"><span>Bin <strong>{p["bin"]}</strong></span><span><strong>{p["count"]}</strong> per kit</span><span class="supervision"><strong>{p["scope"]}</strong> supervision</span></div><nav class="downloads" aria-label="Downloads"><a href="field-card.pdf" download="part-reference-{pid}.pdf">Part reference sheet PDF</a><a href="bag-label.pdf" download>Avery label PDF</a><a href="../../downloads/frk-part-reference.pdf" download>Offline reference PDF</a></nav>'
            if p.get('notice'):
                body += '<p class="notice">'+html.escape(p['notice'])+'</p>'
            if len(p['applications']) > 1:
                body += '<nav class="applications" aria-label="Applications">'+''.join(f'<a href="#{a["id"]}">{MODELS[a["model"]]} — {html.escape(application_name(a))}</a>' for a in p['applications'])+'</nav>'
            for a in p['applications']:
                figure = a['id']+'.png'
                alt = f'{MODELS[a["model"]]} {a["name"]}, item {a["item"]} highlighted'
                body += f'<section class="application" id="{a["id"]}"><div><h2>{MODELS[a["model"]]} — {html.escape(application_name(a))}</h2><p>{self.render_text(a["text"], "html")}</p><p class="facts">Item {a["item"]} <span>Qty listed: {a["quantity"]}</span></p></div><a class="diagram" href="{figure}" aria-label="Open full-size diagram: {html.escape(alt)}"><img src="{figure}" alt="{html.escape(alt)}" loading="lazy"></a><p class="source">{html.escape(self.source(a))}</p></section>'
            body += f'<p class="correction"><a href="{html.escape(self.correction_url(p), quote=True)}">Suggest a correction</a><small>GitHub · sign-in required</small></p>'
            write(f'docs/parts/{pid}/index.html', self.html_page(p['name'], body, 2))
        index += '</ul><p id="empty" hidden>No matching parts.</p><script src="assets/search.js" defer></script>'
        write('docs/index.html', self.html_page('Parts', index))


def compile_pdf(source, passes=1, destination=None, executable='pdflatex'):
    source = ROOT / source
    destination = ROOT / destination if destination else source.with_suffix('.pdf')
    output = ROOT / '.build/latex' / destination.relative_to(ROOT).with_suffix('')
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
    if destination.exists() and stamp.exists() and stamp.read_text() == fingerprint:
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
    copy(output/source.with_suffix('.pdf').name, destination)
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
    correction_urls = {ref.correction_url(p) for p in ref.parts.values()}
    html_pages = {path.resolve(): PageLinks(path.read_text()) for path in (ROOT/'docs').rglob('*.html')}
    if len(html_pages) != len(ref.parts)+1:
        raise ValueError('Website page count differs from the inventory')
    pages = dict(html_pages)
    for path in (ROOT/'docs/parts').rglob('README.md'):
        content = path.read_text()
        page = PageLinks(content)
        page.links.extend(re.findall(r'\[[^\]]+\]\(([^\s)]+)\)', content))
        pages[path.resolve()] = page
    if len(pages)-len(html_pages) != len(ref.parts)+1:
        raise ValueError('Git reference page count differs from the inventory')
    count = 0
    for path, page in pages.items():
        for link in page.links:
            url = urlsplit(link)
            if url.scheme or url.netloc:
                if path.suffix == '.md' or link in correction_urls:
                    continue
                raise ValueError(f'Unexpected external dependency in site: {link}')
            target = (path.parent/unquote(url.path)).resolve() if url.path else path
            if target.is_dir():
                target /= 'index.html'
            if not target.is_file():
                raise ValueError(f'Broken site link: {path} → {link}')
            if url.fragment and (target not in pages or url.fragment not in pages[target].ids):
                raise ValueError(f'Broken site anchor: {path} → {link}')
            count += 1
    print(f'Checked {len(html_pages)} web pages, {len(pages)-len(html_pages)} Git reference pages and {count} local links/assets.')


def check_live_site(ref):
    """Check deployed QR destinations, including HTTP-200 error or stale pages."""
    def verify(path, url):
        try:
            with urlopen(Request(url, headers={'User-Agent': 'FRK-reference-check'}), timeout=25) as response:
                content = response.read()
        except (HTTPError, URLError) as error:
            raise ValueError(f'Cannot open {url}: {error}. Check the Pages publishing source and deployment status.') from error
        if content != (ROOT/'docs'/path).read_bytes():
            raise ValueError(f'Published page differs from the generated reference: {url}')

    verify('index.html', ref.url+'/')
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda key: verify(f'parts/{key}/index.html', ref.part_url(key)), ref.parts))
    print(f'Checked the live index and all {len(ref.parts)} QR destinations at {ref.url}/.')


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
            path = ROOT/f'docs/parts/{p["id"]}/{file}.pdf'
            pdf = PdfReader(path)
            if len(pdf.pages) != 1:
                raise ValueError(f'Expected a single page: {path}')
            text = pdf.pages[0].extract_text()
            if identifier(p['number']) not in identifier(text):
                raise ValueError(f'Part number missing from {path}')
            if identifier(p['name']) not in identifier(text):
                raise ValueError(f'Part name missing from {path}')
            bin_text = rf'\bBin\s*{p["bin"]}' + (r'(?=\s*Qty)' if file == 'bag-label' else r'\b')
            if not re.search(bin_text, text):
                raise ValueError(f'Incorrect bin in {path}')
            quantity = rf'(?<!\d){p["count"]}\s*spare' if file == 'field-card' else rf'Qty\s*{p["count"]}(?!\d)'
            scope = rf'\b{p["scope"]}\s*supervision' if file == 'field-card' else rf'\b{p["scope"]}\b'
            if not re.search(quantity, text) or not re.search(scope, text):
                raise ValueError(f'Inventory quantity or supervision differs in {path}')
            if file == 'bag-label':
                check_label_position(pdf.pages[0], p['number'])
        card = PdfReader(ROOT/f'docs/parts/{p["id"]}/field-card.pdf')
        uris = {str(a.get_object().get('/A', {}).get('/URI', '')) for a in card.pages[0].get('/Annots', [])}
        if ref.part_url(p['id']) not in uris:
            raise ValueError(f'Part number/QR link missing: {p["id"]}')
        if ref.correction_url(p) not in uris:
            raise ValueError(f'Correction link missing: {p["id"]}')
        for a in p['applications']:
            for key, anchor in LINK.findall(a['text']):
                if ref.part_url(key, anchor) not in uris:
                    raise ValueError(f'Related part link missing: {p["id"]} → {key}')
    labels = PdfReader(ROOT/'frk-parts-labels-avery.pdf')
    if len(labels.pages) != math.ceil(len(ref.parts)/30):
        raise ValueError('Incorrect Avery sheet count')
    for i, p in enumerate(ref.parts.values()):
        check_label_position(labels.pages[i//30], p['number'], i%3, (i%30)//3)
    for filename in ('frk-bin-labels-avery.pdf', 'frk-parts-boxmap.pdf'):
        pdf = PdfReader(ROOT/filename)
        if len(pdf.pages) != 1:
            raise ValueError(f'Expected a single kit sheet: {filename}')
        text = pdf.pages[0].extract_text()
        for key in ref.bins():
            if len(re.findall(rf'\b{key}\b', text)) != 1:
                raise ValueError(f'Missing or duplicate bin in {filename}: {key}')
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
    print(f'Checked {len(ref.parts)} reference sheets, {len(ref.parts)} labels, {len(book.pages)} offline pages and {internal_links} internal PDF links.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', nargs='?', default='all', choices=['all', 'generate', 'check', 'check-live'])
    parser.add_argument('--pdflatex', default=os.environ.get('PDFLATEX', 'pdflatex'))
    args = parser.parse_args()
    ref = Reference()
    if args.target == 'check-live':
        check_live_site(ref)
        return
    if args.target == 'check':
        check_pdfs(ref)
        check_site(ref)
        return
    ref.assets()
    ref.sources()
    if args.target == 'generate':
        return
    jobs = [(f'.build/parts/{p["id"]}/{name}.tex', 2 if name == 'bag-label' else 1, f'docs/parts/{p["id"]}/{name}.pdf') for p in ref.parts.values() for name in ('field-card', 'bag-label')]
    jobs += [('frk-part-reference.tex', 3), ('frk-parts-labels-avery.tex', 2), ('frk-bin-labels-avery.tex', 2), ('frk-parts-inventory.tex', 2), ('frk-parts-boxmap.tex', 2)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda job: compile_pdf(*job, executable=args.pdflatex), jobs))
    ref.site()
    check_pdfs(ref)
    check_site(ref)
    print(f'Built {args.target}.')


if __name__ == '__main__':
    main()

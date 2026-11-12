#!/usr/bin/env python3
"""xlsx_to_md.py — body of combo-fileconv-xlsx-to-md/convert.sh.

Usage:
    xlsx_to_md.py <input> <output.md|''> <list_only 0|1> [<records.jsonl>]

The Markdown it writes is the long-standing one-table-per-sheet rendering of
cached cell values. When a records path is given it additionally writes
source-addressed records (JSONL) that keep what the Markdown flattens away:
sheet visibility and merged ranges, per-cell coordinates, cached value and
formula *separately*, number format, comments, and the text of shapes /
text boxes in the sheet drawings with their anchor cell. Formulas are never
recalculated: a formula whose result was not cached has value null.
"""
import sys
import os

try:
    import openpyxl as _xl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

try:
    import xlrd as _xr
    HAS_XLRD = True
except ImportError:
    HAS_XLRD = False

input_path  = sys.argv[1]
output_path = sys.argv[2] if len(sys.argv) > 2 else ''
list_only   = sys.argv[3] == '1' if len(sys.argv) > 3 else False

ext = os.path.splitext(input_path)[1].lower()

if ext in ('.xlsx', '.xlsm'):
    if not HAS_OPENPYXL:
        print('Error: openpyxl is required for .xlsx files.', file=sys.stderr)
        sys.exit(1)
elif ext == '.xls':
    if not HAS_XLRD:
        print('Error: xlrd is required for legacy .xls files.', file=sys.stderr)
        sys.exit(1)
else:
    if not HAS_OPENPYXL and not HAS_XLRD:
        print('Error: neither openpyxl nor xlrd is installed.', file=sys.stderr)
        sys.exit(1)

# USE_OPENPYXL tracks which library actually opened this workbook. It must not
# be confused with HAS_OPENPYXL (merely: openpyxl is importable). A legacy .xls
# is always read with xlrd, even on a machine that also has openpyxl installed.
if ext in ('.xlsx', '.xlsm') and HAS_OPENPYXL:
    wb = _xl.load_workbook(input_path, read_only=True, data_only=True)
    USE_OPENPYXL = True
elif HAS_XLRD:
    wb = _xr.open_workbook(input_path)
    USE_OPENPYXL = False
elif HAS_OPENPYXL:
    wb = _xl.load_workbook(input_path, read_only=True, data_only=True)
    USE_OPENPYXL = True
else:
    print('Error: unsupported format.', file=sys.stderr)
    sys.exit(1)

def is_blank(v):
    # openpyxl yields None for an empty cell; str(None) is 'None', so None must
    # be tested before falling back to the string form.
    return v is None or str(v).strip() == ''

def trim_cells(rows):
    trimmed = []
    for row in rows:
        r = list(row)
        while r and is_blank(r[-1]):
            r.pop()
        if r:
            trimmed.append(r)
    while trimmed and all(is_blank(c) for c in trimmed[-1]):
        trimmed.pop()
    return trimmed

def rows_to_md(rows):
    if not rows:
        return ''
    ncols = max(len(r) for r in rows)
    lines = []
    for i, row in enumerate(rows):
        # Replace newlines inside cells so each table row stays on one line.
        # Markdown renderers that support HTML (<br>) will display them;
        # plain-text viewers see the literal <br>, which is still readable.
        NL = chr(10)   # newline
        BS = chr(92)   # backslash
        cells = []
        for c in row:
            s = str(c) if c is not None else ''
            s = s.replace(BS, BS+BS)       # backslash → double backslash (for | escape)
            s = s.replace('|', BS+'|')     # pipe chars
            s = s.replace(NL, '<br>')      # newlines → <br>
            cells.append(s.strip())
        while len(cells) < ncols:
            cells.append('')
        lines.append('| ' + ' | '.join(cells) + ' |')
        if i == 0:
            lines.append('| ' + ' | '.join(['---'] * ncols) + ' |')
    return '\n'.join(lines) + '\n\n'

sheet_names = list(wb.sheetnames) if USE_OPENPYXL else list(wb.sheet_names())

if list_only:
    for name in sheet_names:
        print(name)
    sys.exit(0)

md_lines = [f'# {os.path.basename(input_path)}\n\n']

for sname in sheet_names:
    try:
        if USE_OPENPYXL:
            ws = wb[sname]
            rows_raw = list(ws.iter_rows(values_only=True))
        else:
            ws = wb.sheet_by_name(sname)
            rows_raw = [[ws.cell_value(r, c) for c in range(ws.ncols)] for r in range(ws.nrows)]
    except Exception as e:
        print(f'Warning: could not read sheet "{sname}": {e}', file=sys.stderr)
        continue

    rows = trim_cells(rows_raw)
    if not rows:
        print(f'Skipping empty sheet: {sname}', file=sys.stderr)
        continue

    md_lines.append(f'## {sname}\n\n')
    md_lines.append(rows_to_md(rows))
    md_lines.append('---\n\n')

output_text = ''.join(md_lines).rstrip() + '\n'

if output_path:
    if os.path.dirname(output_path):
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(output_text)
else:
    sys.stdout.write(output_text)


# ------------------------------------------------------------------ records
def _jsonable(v):
    import datetime
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    return str(v)


def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def _extractor_rev():
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        rev = subprocess.run(['git', '-C', here, 'rev-parse', '--short', 'HEAD'],
                             capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(['git', '-C', here, 'status', '--porcelain', '--', '.'],
                               capture_output=True, text=True, check=True).stdout.strip()
        return rev + ('-dirty' if dirty else '')
    except Exception:
        return 'unknown'


def _drawing_records(path):
    """Shapes/text boxes/pictures from xl/drawings, keyed to sheet names."""
    import zipfile, posixpath
    from xml.etree import ElementTree as ET
    NS = {
        'm': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
        'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
        'pr': 'http://schemas.openxmlformats.org/package/2006/relationships',
        'xdr': 'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing',
        'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    }
    RID = '{%s}id' % NS['r']
    out = []
    z = zipfile.ZipFile(path)
    names = set(z.namelist())

    def rels(part):
        rp = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels')
        if rp not in names:
            return {}
        res = {}
        for r in ET.fromstring(z.read(rp)).findall('pr:Relationship', NS):
            tgt = r.get('Target')
            if r.get('TargetMode') != 'External':
                tgt = posixpath.normpath(posixpath.join(posixpath.dirname(part), tgt)) if not tgt.startswith('/') else tgt.lstrip('/')
            res[r.get('Id')] = (r.get('Type', '').rsplit('/', 1)[-1], tgt)
        return res

    wb_rels = rels('xl/workbook.xml')
    wbx = ET.fromstring(z.read('xl/workbook.xml'))
    for sh in wbx.findall('m:sheets/m:sheet', NS):
        sheet_part = wb_rels.get(sh.get(RID), (None, None))[1]
        if not sheet_part or sheet_part not in names:
            continue
        for kind, dpart in rels(sheet_part).values():
            if kind != 'drawing' or dpart not in names:
                continue
            drels = rels(dpart)
            root = ET.fromstring(z.read(dpart))
            for ai, anc in enumerate(list(root)):
                frm = anc.find('xdr:from', NS)
                if frm is not None:
                    col = int(frm.findtext('xdr:col', '0', NS)); row = int(frm.findtext('xdr:row', '0', NS))
                    anchor = f'{get_column_letter(col + 1)}{row + 1}'
                else:
                    anchor = None
                for sp in anc.iter('{%s}sp' % NS['xdr']):
                    txt = '\n'.join(''.join(t.text or '' for t in p.iter('{%s}t' % NS['a']))
                                    for p in sp.iter('{%s}p' % NS['a']))
                    txt = txt.strip()
                    if txt:
                        nv = sp.find('xdr:nvSpPr/xdr:cNvPr', NS)
                        out.append({'kind': 'shape', 'sheet': sh.get('name'), 'anchor': anchor,
                                    'drawing': dpart, 'index': ai,
                                    'name': nv.get('name') if nv is not None else None, 'text': txt})
                for pic in anc.iter('{%s}pic' % NS['xdr']):
                    blip = pic.find('.//a:blip', NS)
                    emb = blip.get('{%s}embed' % NS['r']) if blip is not None else None
                    lnk = blip.get('{%s}link' % NS['r']) if blip is not None else None
                    rid = emb or lnk
                    out.append({'kind': 'image', 'sheet': sh.get('name'), 'anchor': anchor,
                                'drawing': dpart, 'index': ai, 'linked': bool(lnk and not emb),
                                'target': drels.get(rid, (None, None))[1] if rid else None})
    return out


def _comment_records(path):
    """{sheet name: {A1 ref: text}} read from each sheet's comments part.
    (openpyxl drops a comment on a merged, non-anchor cell.)"""
    import zipfile, posixpath
    from xml.etree import ElementTree as ET
    M = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    PR = '{http://schemas.openxmlformats.org/package/2006/relationships}'
    RID = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
    z = zipfile.ZipFile(path)
    names = set(z.namelist())

    def rels(part):
        rp = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels')
        out = {}
        if rp in names:
            for r in ET.fromstring(z.read(rp)).iter(PR + 'Relationship'):
                tgt = r.get('Target', '')
                if r.get('TargetMode') != 'External':
                    tgt = tgt.lstrip('/') if tgt.startswith('/') else posixpath.normpath(posixpath.join(posixpath.dirname(part), tgt))
                out[r.get('Id')] = (r.get('Type', '').rsplit('/', 1)[-1], tgt)
        return out

    wb_rels = rels('xl/workbook.xml')
    res = {}
    for sh in ET.fromstring(z.read('xl/workbook.xml')).iter(M + 'sheet'):
        part = wb_rels.get(sh.get(RID), (None, None))[1]
        if not part or part not in names:
            continue
        for kind, cpart in rels(part).values():
            if kind != 'comments' or cpart not in names:
                continue
            for c in ET.fromstring(z.read(cpart)).iter(M + 'comment'):
                t = c.find(M + 'text')
                res.setdefault(sh.get('name'), {})[c.get('ref')] = \
                    ''.join(x.text or '' for x in t.iter(M + 't')) if t is not None else ''
    return res


def write_records(input_path, records_path):
    if not (ext in ('.xlsx', '.xlsm') and HAS_OPENPYXL):
        print('Error: --records needs openpyxl and an .xlsx/.xlsm input (legacy .xls is not supported).', file=sys.stderr)
        sys.exit(1)
    wb_f = _xl.load_workbook(input_path, data_only=False)   # formulas, comments, merges
    wb_v = _xl.load_workbook(input_path, data_only=True, read_only=True)  # cached results
    recs = []
    stats = {'cells': 0, 'formulas': 0, 'uncached_formulas': 0, 'comments': 0, 'merged': 0}
    all_comments = _comment_records(input_path)
    for si, ws in enumerate(wb_f.worksheets):
        merged = [str(r) for r in ws.merged_cells.ranges]
        anchors = {str(r).split(':')[0]: str(r) for r in ws.merged_cells.ranges}
        stats['merged'] += len(merged)
        recs.append({'kind': 'sheet', 'id': f'sheet:{ws.title}', 'sheet': ws.title, 'index': si,
                     'state': ws.sheet_state, 'dimensions': ws.dimensions, 'merged': merged})
        comments = dict(all_comments.get(ws.title, {}))
        cached = {}
        for row in wb_v[ws.title].iter_rows():
            for c in row:
                if getattr(c, 'value', None) is not None:
                    cached[c.coordinate] = c.value
        cells = [c for row in ws.iter_rows() for c in row]
        # a comment outside the used range still gets its cell record
        cells += [ws[ref] for ref in comments if ws[ref].coordinate not in {c.coordinate for c in cells}]
        for c in cells:
                cmt = comments.pop(c.coordinate, None)
                if c.data_type != 'f' and cmt is None and (c.value is None or c.value == ''):
                    continue  # policy: formula, comment or non-empty value
                r = {'kind': 'cell', 'id': f'cell:{ws.title}!{c.coordinate}', 'sheet': ws.title,
                     'coord': c.coordinate, 'row': c.row, 'col': c.column}
                if c.data_type == 'f':
                    f = c.value
                    if isinstance(f, str):
                        r['formula'] = f
                    else:
                        # ArrayFormula / DataTableFormula: keep type and attributes
                        # structurally instead of an unstable object repr.
                        attrs = {k: v for k, v in vars(f).items() if v is not None and not k.startswith('_')}
                        r['formula'] = attrs.pop('text', None)
                        r['formula_type'] = type(f).__name__
                        r['formula_attrs'] = {k: _jsonable(v) for k, v in attrs.items()}
                    r['value'] = _jsonable(cached.get(c.coordinate))
                    stats['formulas'] += 1
                    if r['value'] is None:
                        stats['uncached_formulas'] += 1
                else:
                    r['value'] = _jsonable(c.value)
                if c.number_format and c.number_format != 'General':
                    r['number_format'] = c.number_format
                if c.coordinate in anchors:
                    r['merge'] = anchors[c.coordinate]
                if cmt is not None:
                    r['comment'] = cmt
                    stats['comments'] += 1
                stats['cells'] += 1
                recs.append(r)
    drawings = _drawing_records(input_path)
    for i, d in enumerate(drawings):
        d['id'] = f"{d['kind']}:{d['sheet']}#{i}"
    recs.extend(drawings)
    stats['shapes'] = sum(1 for d in drawings if d['kind'] == 'shape')
    stats['images'] = sum(1 for d in drawings if d['kind'] == 'image')
    meta = {'type': 'meta', 'source': os.path.abspath(input_path), 'sha256': _sha256(input_path),
            'extractor': 'combo-fileconv-xlsx-to-md/xlsx_to_md.py', 'extractor_rev': _extractor_rev(),
            'libs': {'openpyxl': _xl.__version__}, 'values': 'cached (not recalculated)', 'stats': stats}
    with open(records_path, 'w', encoding='utf-8') as f:
        f.write(json.dumps(meta, ensure_ascii=False) + '\n')
        for r in recs:
            f.write(json.dumps(dict(type='record', **r), ensure_ascii=False) + '\n')
    print(f'  → Records: {records_path} ({stats})')


if len(sys.argv) > 4 and sys.argv[4]:
    import json
    from openpyxl.utils import get_column_letter
    write_records(input_path, sys.argv[4])

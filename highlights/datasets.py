"""Portable, checksummed demo bundle; never replaces an existing data directory."""
import hashlib
import json
from pathlib import Path,PurePosixPath
import shutil
import tempfile
import zipfile
from .errors import AppError

def initialize(destination,bundle=None):
    dest=Path(destination).expanduser().absolute()
    if dest.exists():raise AppError('data_exists','Data directory already exists; choose a new --data path',409)
    dest.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.highlights-',dir=dest.parent) as tmp:
        staging=Path(tmp)/'data';staging.mkdir()
        if bundle:
            with zipfile.ZipFile(bundle) as z:
                infos=z.infolist();names=[i.filename for i in infos]
                if len(set(names))!=len(names) or len(names)>2000 or sum(i.file_size for i in infos)>128*1024*1024:
                    raise AppError('invalid_bundle','Duplicate entries or demo bundle too large')
                manifest=json.loads(z.read('bundle.json'))
                if manifest.get('format')!='highlights-demo-v1' or set(names)!=set(manifest['files'])|{'bundle.json'}:
                    raise AppError('invalid_bundle','Invalid bundle manifest')
                for name,digest in manifest['files'].items():
                    path=PurePosixPath(name)
                    if path.is_absolute() or '..' in path.parts or '\\' in name or not path.parts or path.parts[0] not in ('epl','dota2'):
                        raise AppError('invalid_bundle','Invalid bundle path')
                    data=z.read(name)
                    if hashlib.sha256(data).hexdigest()!=digest:raise AppError('invalid_bundle','Bundle checksum mismatch')
                    out=staging.joinpath(*path.parts);out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(data)
                # Catalog entries can only refer to files checked above.
                for mode in ('epl','dota2'):
                    catalog=json.loads((staging/mode/'catalog.json').read_text())
                    for row in catalog['matches']:
                        name=f'{mode}/{row["file"]}'
                        if name not in manifest['files'] or '..' in PurePosixPath(name).parts:
                            raise AppError('invalid_bundle','Catalog points outside the bundle')
        (staging/'workspace.json').write_text(json.dumps(dict(format='highlights-workspace-v1'))+'\n')
        # Rename within one filesystem; destination must still be absent.
        if dest.exists():raise AppError('data_exists','Data directory appeared during initialization',409)
        staging.rename(dest)
    return dict(status='initialized',data=str(dest),demo=bool(bundle))

def make_demo_bundle(source,output):
    source=Path(source);output=Path(output)
    if output.exists():raise AppError('output_exists','Refusing to overwrite demo bundle',409)
    files={}
    for mode in ('epl','dota2'):
        catalog=source/mode/'catalog.json';rows=json.loads(catalog.read_text())['matches']
        for p in [catalog]+[source/mode/r['file'] for r in rows]:
            name=str(p.relative_to(source));files[name]=p.read_bytes()
    manifest=dict(format='highlights-demo-v1',sources=['StatsBomb Open Data','OpenDota'],
                  purpose='Historical EPL/Dota demo snapshot; no live feed or complete career coverage.',
                  files={n:hashlib.sha256(b).hexdigest() for n,b in files.items()})
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('bundle.json',json.dumps(manifest,ensure_ascii=False))
        for n,b in files.items():z.writestr(n,b)
    return dict(path=str(output.resolve()),sha256=hashlib.sha256(output.read_bytes()).hexdigest(),files=len(files))

#!/usr/bin/env python3
"""Apply the minimal queue integration fix to the pinned ARM Neu source."""
from pathlib import Path


def destination_dir(final_dir, rendered_folder):
    """Use ARM's job directory when it already ends in the rendered folder."""
    import os
    if not rendered_folder:
        return final_dir
    base=os.path.normpath(final_dir).split(os.sep)
    folder=os.path.normpath(rendered_folder).split(os.sep)
    if len(folder)<=len(base) and base[-len(folder):]==folder:
        return final_dir
    return os.path.join(final_dir,rendered_folder)


def apply(source_root):
    source_root=Path(source_root)
    version=(source_root/'VERSION').read_text().strip()
    if version!='19.1.0':
        raise SystemExit(f'Expected verified ARM Neu 19.1.0 source; got {version!r}')
    source=source_root/'arm/ripper/naming.py'
    text=source.read_text()
    old='''    rendered_folder = r.get("rendered_folder", '')
    dest_dir = os.path.join(final_dir, rendered_folder) if rendered_folder else final_dir
'''
    path_already_patched='def _rendered_destination_dir(' in text
    if path_already_patched:
        if 'dest_dir = _rendered_destination_dir(final_dir, rendered_folder)' not in text:
            raise SystemExit('Unexpected previously patched ARM naming.py')
    elif text.count(old)!=1:
        raise SystemExit('ARM naming.py does not match the inspected finalization source; refusing patch')
    job_source=source_root/'arm/models/job.py'
    job_text=job_source.read_text()
    old_eject='''        self.drive.eject()
        self.ejected = True
'''
    if 'AUTO_EJECT failed:' in job_text:
        if 'self.ejected = True' not in job_text:
            raise SystemExit('Unexpected previously patched ARM job eject logic')
    elif job_text.count(old_eject)!=1:
        raise SystemExit('ARM Job.eject does not match inspected source; refusing patch')
    else:
        job_text=job_text.replace(old_eject,'''        eject_error = self.drive.eject()
        if eject_error:
            error = f"AUTO_EJECT failed: {eject_error}"
            self.errors = f"{self.errors}; {error}" if self.errors else error
            db.session.commit()
            logging.error(error)
            return
        self.ejected = True
        db.session.commit()
''',1)
        compile(job_text,str(job_source),'exec')
        job_source.write_text(job_text)
    helper='''def _rendered_destination_dir(final_dir, rendered_folder):
    """Avoid appending a track folder already contained in job.path."""
    if not rendered_folder:
        return final_dir
    base = os.path.normpath(final_dir).split(os.sep)
    folder = os.path.normpath(rendered_folder).split(os.sep)
    if len(folder) <= len(base) and base[-len(folder):] == folder:
        return final_dir
    return os.path.join(final_dir, rendered_folder)


'''
    anchor='def _move_track(track, raw_path, final_dir, rendered_map):\n'
    if text.count(anchor)!=1:
        raise SystemExit('ARM _move_track anchor changed; refusing patch')
    if not path_already_patched:
        text=text.replace(anchor,helper+anchor,1).replace(old,'''    rendered_folder = r.get("rendered_folder", '')
    dest_dir = _rendered_destination_dir(final_dir, rendered_folder)
''',1)
    text=text.replace('''    os.makedirs(dest_dir, exist_ok=True)
    shutil.move(src, os.path.join(dest_dir, dest_name))
''','''    os.makedirs(dest_dir, exist_ok=True)
    # The queue service joins ARM's group to perform safe post-rip renames.
    os.chmod(dest_dir, os.stat(dest_dir).st_mode | 0o770)
    shutil.move(src, os.path.join(dest_dir, dest_name))
''',1)
    compile(text,str(source),'exec')
    source.write_text(text)


if __name__=='__main__':
    import sys
    if len(sys.argv)!=2:
        raise SystemExit('usage: apply_path_fix.py /opt/arm')
    apply(sys.argv[1])

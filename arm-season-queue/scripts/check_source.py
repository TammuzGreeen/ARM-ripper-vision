"""Read-only source verification. Run where the deployed ARM source is visible."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source_root',type=Path,help='Actual ARM container source root')
    args = parser.parse_args()
    reference = json.loads((Path(__file__).with_name('reference-source.json')).read_text('utf-8'))
    results = {}
    for filename,expected in reference['files'].items():
        path = args.source_root/filename
        if not path.is_file():
            results[filename]='missing'
        else:
            # Git checkouts can have CRLF; compare normalised source bytes.
            actual = hashlib.sha256(path.read_bytes().replace(b'\r\n',b'\n')).hexdigest()
            results[filename]='match' if actual==expected else 'DIFFERS'
    passed = all(v=='match' for v in results.values())
    print(json.dumps({'reference_sha':reference['sha'],'matches':passed,'files':results},indent=2))
    if passed:
        print('Source profile matches. After checking the live API and deployment settings, set ARM_VERIFIED_SOURCE_SHA='+reference['sha'])
    raise SystemExit(0 if passed else 1)


if __name__=='__main__':
    main()

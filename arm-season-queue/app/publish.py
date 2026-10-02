"""Run this helper from a verified FileFlows command/script node, once per ready file."""
import argparse
import json
from pathlib import Path

from .handover import publish_manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('manifest',type=Path)
    parser.add_argument('--media-root',type=Path,default=Path('/media'))
    parser.add_argument('--library',type=Path,default=Path('/library'))
    parser.add_argument('--handover',type=Path,default=Path('/handover'))
    args = parser.parse_args()
    if not args.manifest.resolve().is_relative_to((args.handover/'ready').resolve()):
        parser.error('Manifest must be inside the configured handover/ready directory')
    print(json.dumps(publish_manifest(args.manifest,args.media_root,args.library,args.handover)))


if __name__=='__main__':
    main()

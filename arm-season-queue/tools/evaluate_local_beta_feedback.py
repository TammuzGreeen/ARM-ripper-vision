"""Build a deterministic review-feedback report without invoking models."""
import argparse
import hashlib
import json
from pathlib import Path


def cases(root):
    for path in sorted((root/'reviews').glob('*.json')):
        try:
            row=json.loads(path.read_text('utf-8'))
        except (OSError,ValueError):
            continue
        if row.get('workflow')!='local-beta-v1' or not row.get('review_history'):
            continue
        history=row['review_history']
        recognition=(row.get('recognition') or {}).get('result') or {}
        touched={}
        for revision in history:
            for source_id,fields in (revision.get('corrections') or {}).items():
                touched.setdefault(source_id,{}).update(fields)
        split='evaluation' if int(hashlib.sha256(str(row.get('job')).encode()).hexdigest()[:8],16)%5==0 else 'development'
        yield {'job':row.get('job'),'event':row.get('event'),'assessment':row.get('status'),
               'split':split,'reviewed_fields':sorted(set().union(*(set(r.get('reviewed_fields') or []) for r in history))),
               'correction_count':sum(len(r.get('corrections') or {}) for r in history),
               'fields_corrected':sorted({key for fields in touched.values() for key in fields}),
               'revision_count':len(history),'model_tags':(row.get('model_metadata') or {}).get('tags',[]),
               'model_digests':(row.get('model_metadata') or {}).get('digests',{}),
               'model_transcriptions':{tag:data.get('transcription') for tag,data in (recognition.get('runs') or {}).items()},
               'combined_fields':(recognition.get('agreement') or {}).get('fields',{}),
               'recognition_policy':(row.get('model_metadata') or {}).get('prompt_version'),
               'corrected_titles':touched}


def report(root, predictions=None):
    rows=list(cases(root))
    result={'schema_version':1,'purpose':'human-reviewed regression/evaluation cases; no model training or prompt mutation',
            'split_policy':'stable SHA-256(job) mod 5; 20% evaluation, 80% development; keep evaluation cases out of tuning',
            'counts':{'total':len(rows),'development':sum(r['split']=='development' for r in rows),
                      'evaluation':sum(r['split']=='evaluation' for r in rows),
                      'corrected_jobs':sum(r['correction_count']>0 for r in rows)},
            'field_correction_counts':{field:sum(field in r['fields_corrected'] for r in rows)
                                       for field in sorted({f for r in rows for f in r['fields_corrected']})},
            'cases':rows}
    if predictions is not None:
        actual=predictions.get('jobs',{})
        scores=[]
        for row in rows:
            predicted=actual.get(str(row['job']),{})
            # Only score human-touched fields, avoiding treating unreviewed data
            # as ground truth. The prediction structure mirrors corrected_titles.
            checks=[]
            for source_id, expected_fields in row['corrected_titles'].items():
                predicted_fields=(predicted.get('titles') or {}).get(str(source_id),{})
                for field, expected in expected_fields.items():
                    value=predicted_fields.get(field)
                    if value is None and field in ('series','season'):
                        value=(predicted.get('identity') or {}).get(field)
                    checks.append({'field':field,'expected':expected,'predicted':value,'match':value==expected})
            scores.append({'job':row['job'],'split':row['split'],'checks':checks,
                           'correct':sum(c['match'] for c in checks),'total':len(checks)})
        result['comparison']={'scoring':'exact equality on human-corrected fields only',
                              'predictions_schema':'{"jobs":{"<job>":{"identity":{},"titles":{"<source_id>":{}}}}}',
                              'counts':{split:{'correct':sum(c['correct'] for c in scores if c['split']==split),
                                               'total':sum(c['total'] for c in scores if c['split']==split)}
                                        for split in ('development','evaluation')},
                              'cases':scores}
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path,default=Path('/state'),help='Private queue state directory')
    parser.add_argument('--output',type=Path,help='Optional private JSON report path; default prints JSON')
    parser.add_argument('--predictions',type=Path,help='Candidate-parser/prompt results in the documented jobs JSON schema')
    args=parser.parse_args()
    predictions=json.loads(args.predictions.read_text('utf-8')) if args.predictions else None
    result=report(args.state,predictions)
    text=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(text,'utf-8')
        args.output.chmod(0o600)
    else:
        print(text,end='')


if __name__=='__main__':
    main()

from pathlib import Path
from arm_ripper_vision.masterlists import MasterlistStore
def test_masterlist(tmp_path:Path):
    (tmp_path/"x.yaml").write_text("""schema_version: 1
id: x
series: {name: Show, imdb_id: tt1}
seasons:
  - season: 1
    discs:
      - disc: 1
        episodes:
          - {episode: 1, title: Pilot, source_title: 0}
""")
    m=MasterlistStore(tmp_path).get("x")
    assert m.flatten_discs()[0].key=="S01D01"

"""Future subtitle evidence boundary. Deliberately not connected to the pipeline."""
from pathlib import Path


def analyze_subtitles(media: Path) -> dict:
    raise NotImplementedError('Subtitle extraction and episode analysis are not implemented')

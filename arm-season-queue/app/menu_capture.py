"""Future menu-capture boundary. No optical-drive access or menu navigation yet."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MenuImage:
    image: Path
    source_id: str
    # Descriptive provenance only; menu order never implies an episode/title mapping.
    navigation_context: str


def capture_menu(source: Path) -> tuple[MenuImage, ...]:
    raise NotImplementedError('Menu capture is planned; no disc or backup was opened')

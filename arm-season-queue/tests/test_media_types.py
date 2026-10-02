import unittest

from app.media_types import LabelEvidence, MediaType, PLANNED_ADAPTERS, PlannedRecognitionAdapter


class FutureMediaTests(unittest.TestCase):
    def test_non_tv_cannot_silently_claim_recognition(self):
        evidence = LabelEvidence('A printed label', ('capture.jpg',), ('1234567890123',))
        for kind in (MediaType.MOVIE, MediaType.MUSIC, MediaType.AUDIOBOOK):
            with self.subTest(kind=kind), self.assertRaises(NotImplementedError):
                PLANNED_ADAPTERS[kind].identify(evidence)

    def test_stub_cannot_replace_existing_tv_recognition(self):
        with self.assertRaises(ValueError):
            PlannedRecognitionAdapter(MediaType.TV)

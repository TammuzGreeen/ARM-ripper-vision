import unittest

from app.agreement import MODEL_ORDER, OPTIONS, PROMPT, evaluate
from test_core import master


class AgreementGateTests(unittest.TestCase):
    def test_three_frame_vision_context_is_not_truncated_to_four_k(self):
        self.assertEqual(OPTIONS['num_ctx'], 8192)
        self.assertEqual(OPTIONS['num_predict'], 1536)
        self.assertEqual(OPTIONS['num_gpu'], 0)

    def test_transcription_prompt_focuses_disc_identity_and_avoids_repeated_fine_print(self):
        for phrase in ('Series/title:', 'Season:', 'Disc:', 'Printed episodes/range:', 'Episode titles:',
                       'Ignore ratings', 'repeated legal text', 'at most 12 short lines'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, PROMPT)

    def transcript(self, *, disc=1, season=2, episodes='1-4', edition='Teil 1'):
        titles = '\n'.join(episode.title for episode in master().discs[disc - 1].episodes)
        return (f'Star Trek Deep Space Nine\nSeason {season}\nDisc {disc}\n{edition}\n'
                f'Episodes {episodes}\n{titles}')

    def result(self, first, second=None, approved=None):
        return evaluate({MODEL_ORDER[0]: first, MODEL_ORDER[1]: second or first},
                        {tag: {'usable': True} for tag in MODEL_ORDER},
                        [approved or master()])

    def test_full_priority_agreement_and_unique_approved_disc_match_pass(self):
        result = self.result(self.transcript())
        self.assertTrue(result['accepted'])
        self.assertEqual(len(result['matches']), 1)
        self.assertEqual(result['matches'][0]['disc'], 'disc-1')

    def test_episode_range_labels_in_english_german_french_and_spanish_are_equivalent(self):
        labels = ('Episodes 1-4', 'Episoden 1-4', 'Épisodes 1-4', 'Episodios 1-4')
        for label in labels:
            with self.subTest(label=label):
                text = self.transcript().replace('Episodes 1-4', label)
                result = self.result(text)
                self.assertTrue(result['accepted'], result['reasons'])

    def test_actual_camera_transcriptions_normalize_to_equivalent_identity(self):
        primary = ('Series/title: STAR TREK VOYAGER\nSeason: 2\nDisc: 1\n'
                   'Printed episodes/range: EPISODES 1-4\nEdition/version:')
        secondary = ('Series/title: STAR TREK VOYAGER\nSeason: 2\nDisc: 1\n'
                     'Printed episodes/range: 1-4\nEdition/version:')
        voyager = master().model_copy(update={
            'series': 'Star Trek Voyager', 'title_aliases': ['STAR TREK VOYAGER']})
        result = evaluate({MODEL_ORDER[0]: primary, MODEL_ORDER[1]: secondary},
                          {tag: {'usable': True} for tag in MODEL_ORDER}, [voyager])
        self.assertTrue(result['accepted'], result['reasons'])
        self.assertEqual(result['normalized_priority_fields'][MODEL_ORDER[0]]['episodes'], [1, 2, 3, 4])
        self.assertEqual(result['normalized_priority_fields'][MODEL_ORDER[1]]['episodes'], [1, 2, 3, 4])
        self.assertEqual(result['normalized_priority_fields'][MODEL_ORDER[0]]['disc_numbers'], [1])
        proof = result['validated_candidates'][0]
        self.assertEqual(proof['primary']['titles']['status'], 'OMITTED')
        self.assertFalse(proof['primary']['titles']['required'])

    def test_episode_range_accepts_spacing_and_unicode_dashes_but_only_in_labeled_field(self):
        for notation in ('1 - 4', '1–4', '1 — 4', '1 − 4'):
            text = self.transcript().replace('Episodes 1-4', f'Episoden: {notation}')
            result = self.result(text)
            self.assertEqual(result['normalized_priority_fields'][MODEL_ORDER[0]]['episodes'], [1, 2, 3, 4])
            self.assertTrue(result['accepted'], result['reasons'])
        unrelated = ('Star Trek Deep Space Nine\nSeason 2\nDisc 1\nCatalog 1-4\n'
                     'Runtime 42-45 minutes\nTeil 1')
        result = self.result(unrelated)
        self.assertEqual(result['normalized_priority_fields'][MODEL_ORDER[0]]['episodes'], [])

    def test_missing_optional_titles_are_not_model_disagreement(self):
        first = self.transcript()
        second = 'Star Trek Deep Space Nine\nSeason 2\nDisc 1\nTeil 1\nEpisodes 1-4'
        result = self.result(first, second)
        self.assertTrue(result['accepted'], result['reasons'])
        self.assertFalse(any(item['field'] == 'titles' for item in result['field_disagreements']))

    def test_missing_required_episode_observation_is_not_filled_from_masterlist(self):
        first = self.transcript()
        second = 'Star Trek Deep Space Nine\nSeason 2\nDisc 1\nTeil 1'
        result = self.result(first, second)
        self.assertTrue(result['accepted'])
        self.assertFalse(any(item['field'] == 'episodes' for item in result['field_disagreements']))
        self.assertIn(MODEL_ORDER[1],result['missing_observations'])
        self.assertEqual(result['normalized_priority_fields'][MODEL_ORDER[1]]['episodes'], [])

    def test_different_explicit_episode_ranges_remain_a_real_disagreement(self):
        result = self.result(self.transcript(episodes='1-4'), self.transcript(episodes='5-8'))
        self.assertTrue(result['accepted'])
        self.assertTrue(result['requires_targeted_review'])
        self.assertIn('episodes', [item['field'] for item in result['field_disagreements']])

    def test_overlapping_but_non_equivalent_episode_ranges_still_conflict(self):
        result = self.result(self.transcript(episodes='1-4'), self.transcript(episodes='1-3'))
        self.assertEqual(result['field_combinations']['episodes']['classification'], 'conflicting')
        self.assertTrue(result['requires_targeted_review'])

    def test_localized_season_and_disc_labels_are_parsed(self):
        variants = (
            ('Staffel 2', 'Disc 1'), ('Season 2', 'Disk 1'),
            ('Saison 2', 'Disque 1'), ('Temporada 2', 'Disco 1'),
        )
        for season, disc in variants:
            with self.subTest(season=season, disc=disc):
                text = self.transcript().replace('Season 2', season).replace('Disc 1', disc)
                self.assertTrue(self.result(text)['accepted'])

    def test_wrong_disc_number_or_season_remains_recognized_but_unmatched(self):
        for text in (self.transcript(disc=2), self.transcript(season=3)):
            with self.subTest(text=text):
                result = self.result(text)
                self.assertTrue(result['accepted'])
                self.assertEqual(result['reference_match']['status'],'none')
                self.assertEqual(result['matches'], [])

    def test_disc_number_conflict_is_targeted_review_without_discarding_other_fields(self):
        result = self.result(self.transcript(), self.transcript(disc=2))
        self.assertTrue(result['accepted'])
        self.assertTrue(result['requires_targeted_review'])
        self.assertTrue(result['field_disagreements'])

    def test_ambiguous_reference_candidates_do_not_reject_model_agreement(self):
        other = master().model_copy(update={'id': 'duplicate-edition'})
        result = evaluate({tag: self.transcript() for tag in MODEL_ORDER},
                          {tag: {'usable': True} for tag in MODEL_ORDER}, [master(), other])
        self.assertTrue(result['accepted'])
        self.assertEqual(result['reference_match']['status'],'ambiguous')

    def test_no_masterlist_match_is_a_normal_recognition_result(self):
        result = evaluate({tag: self.transcript() for tag in MODEL_ORDER},
                          {tag: {'usable': True} for tag in MODEL_ORDER}, [])
        self.assertTrue(result['accepted'])
        self.assertEqual(result['reference_match']['status'],'none')
        self.assertEqual(result['matches'], [])

    def test_ds9_capture_combines_franchise_partial_and_omitted_season(self):
        primary = ('Media type: DVD\nSeries/title: STAR TREK DEEP SPACE NINE\n'
                   'Season: 2\nDisc: 1\nPrinted episodes/range: 1-4\n'
                   'Catalogue identifier: VFB 25451')
        secondary = ('Media type: DVD\nSeries/title: STAR TREK\n'
                     'Season: DEEP SPACE NINE\nDisc: 1\n'
                     'Printed episodes/range: 1-4\nEpisode titles: DISC 1 : EPISODES 1-4\n'
                     'Artist: CBS DVD\nAlbum: DOLBY\nAuthor: CBS STUDIOS INC.\n'
                     'Edition/version: R1\nCatalogue identifier: EU 113126')
        result = evaluate({MODEL_ORDER[0]: primary, MODEL_ORDER[1]: secondary},
                          {tag: {'usable': True} for tag in MODEL_ORDER}, [master()])
        agreement = result
        self.assertTrue(agreement['accepted'], agreement['reasons'])
        self.assertEqual(agreement['working_identification']['series_display'], 'Star Trek: Deep Space Nine')
        self.assertEqual(agreement['working_identification']['season'], 2)
        self.assertEqual(agreement['working_identification']['disc_number'], 1)
        self.assertEqual(agreement['working_identification']['episodes'], [1, 2, 3, 4])
        self.assertEqual(agreement['field_combinations']['series']['classification'], 'compatible_partial')
        self.assertEqual(agreement['field_combinations']['season']['classification'], 'single-source')
        self.assertEqual(agreement['field_combinations']['episodes']['classification'], 'corroborated')
        self.assertEqual(agreement['reference_match']['status'], 'unique')
        parsed_secondary = agreement['normalized_priority_fields'][MODEL_ORDER[1]]
        self.assertEqual(parsed_secondary['series'], ['star trek'])
        self.assertEqual(parsed_secondary['seasons'], [])
        self.assertEqual(parsed_secondary['descriptive']['artist'], ['CBS DVD'])
        self.assertNotIn('cbs dvd', parsed_secondary['series'])
        self.assertEqual(parsed_secondary['episode_titles'], [])

    def test_genuine_series_and_season_conflicts_are_field_scoped(self):
        left = 'Series/title: Star Trek: Deep Space Nine\nSeason: 2\nDisc: 1\nEpisodes: 1-4'
        right = 'Series/title: Star Trek Voyager\nSeason: 3\nDisc: 1\nEpisodes: 1-4'
        result = evaluate({MODEL_ORDER[0]: left, MODEL_ORDER[1]: right},
                          {tag: {'usable': True} for tag in MODEL_ORDER}, [master()])
        self.assertEqual(set(item['field'] for item in result['field_disagreements']),
                         {'series', 'season'})
        self.assertEqual(result['working_identification']['disc_number'], 1)
        self.assertEqual(result['working_identification']['episodes'], [1, 2, 3, 4])
        self.assertTrue(result['accepted'])
        self.assertEqual(result['status'], 'CONFLICT_REVIEW')

    def test_no_masterlists_preserves_single_source_identity_without_match(self):
        primary = 'Series/title: Star Trek: Deep Space Nine\nSeason: 2\nDisc: 1'
        secondary = 'Series/title: Star Trek\nDisc: 1'
        result = evaluate({MODEL_ORDER[0]: primary, MODEL_ORDER[1]: secondary},
                          {tag: {'usable': True} for tag in MODEL_ORDER}, [])
        self.assertTrue(result['accepted'])
        self.assertEqual(result['reference_match']['status'], 'none')
        self.assertEqual(result['working_identification']['season'], 2)
        self.assertFalse(result['rip_authorization_eligible'])

    def test_ambiguous_explicit_edition_tokens_are_reference_uncertainty(self):
        original = master()
        other = original.model_copy(update={
            'id': 'alternate-edition', 'edition': 'de-dvd-s02-alt',
            'edition_name': 'Alternate German DVD', 'edition_tokens': ['Teil 2']})
        text = self.transcript() + '\nTeil 2'
        result = evaluate({tag: text for tag in MODEL_ORDER},
                          {tag: {'usable': True} for tag in MODEL_ORDER}, [original, other])
        self.assertTrue(result['accepted'])
        self.assertEqual(result['reference_match']['status'],'ambiguous')

    def test_multiple_printed_identifier_candidates_are_reference_uncertainty(self):
        original = master()
        original_disc = original.discs[0].model_copy(update={'printed_identifiers': ['LABEL ONE']})
        original = original.model_copy(update={'discs': [original_disc, *original.discs[1:]]})
        other_disc = original_disc.model_copy(update={'printed_identifiers': ['LABEL TWO']})
        other = original.model_copy(update={'id': 'alternate-label',
                                            'edition': 'de-dvd-s02-alt',
                                            'edition_name': 'Alternate German DVD',
                                            'discs': [other_disc, *original.discs[1:]]})
        text = self.transcript() + '\nLABEL ONE\nLABEL TWO'
        result = evaluate({tag: text for tag in MODEL_ORDER},
                          {tag: {'usable': True} for tag in MODEL_ORDER}, [original, other])
        self.assertTrue(result['accepted'])
        self.assertEqual(result['reference_match']['status'],'ambiguous')


if __name__ == '__main__':
    unittest.main()

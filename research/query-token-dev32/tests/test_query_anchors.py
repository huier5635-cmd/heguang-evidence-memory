import importlib
import importlib.util
from pathlib import Path
import sys
import threading
import unittest

ROOT=Path(__file__).resolve().parents[2]
PACKAGE=Path(__file__).resolve().parents[1]/'candidate/heguang_code'
NAME='qianora_dev32_owned_checks'
spec=importlib.util.spec_from_file_location(NAME,PACKAGE/'__init__.py',submodule_search_locations=[str(PACKAGE)])
package=importlib.util.module_from_spec(spec);sys.modules[NAME]=package;spec.loader.exec_module(package)
anchors=importlib.import_module(NAME+'.query_anchors')
reranker=importlib.import_module(NAME+'.reranker')

def encoded(text):return list(range(1000,1000+len(text))),[(i,i+1) for i in range(len(text))]
def selected(text):
    ids,offsets=encoded(text)
    return anchors.preserve_query_anchors(text,ids,offsets)

class QueryEvidenceTests(unittest.TestCase):
    def test_short_inputs_keep_all_original_ids(self):
        for text in ['','simple question','PacketLedger.commit_batch() should preserve source order']:
            ids,_=encoded(text);self.assertEqual(selected(text),ids)

    def test_unstructured_long_input_is_byte_equivalent_to_parent_token_view(self):
        text='ordinary background words '*40;ids,_=encoded(text)
        self.assertEqual(selected(text),ids[:128]+ids[-64:])

    def test_complete_middle_function_survives_where_parent_cannot_represent_it(self):
        anchor='PacketLedger.commit_batch';text='background '*35+'`'+anchor+'` must retain order. '+'environment '*35
        ids,_=encoded(text);start=text.index(anchor);needed=set(ids[start:start+len(anchor)])
        self.assertFalse(needed<=set(ids[:128]+ids[-64:]))
        self.assertTrue(needed<=set(selected(text)))
        self.assertLessEqual(len(selected(text)),192)

    def test_middle_source_path_and_distinct_call_survive_together(self):
        text='background '*35+'Change src/lease.py and call commit_batch(). '+'environment '*35
        result=set(selected(text));ids,_=encoded(text)
        for value in ['src/lease.py','commit_batch']:
            start=text.index(value);self.assertTrue(set(ids[start:start+len(value)])<=result)

    def test_unicode_prefix_uses_character_offsets(self):
        text='中文🙂 '*80+'`FrameMux.flush_batch` should preserve order. '+'notes '*80
        ids,_=encoded(text);start=text.index('FrameMux.flush_batch')
        self.assertTrue(set(ids[start:start+len('FrameMux.flush_batch')])<=set(selected(text)))

    def test_code_names_found_only_in_urls_do_not_change_the_parent_view(self):
        text='background '*35+'https://example.invalid/src/lease.py?retry_count=4 '+'environment '*35
        ids,_=encoded(text);self.assertEqual(selected(text),ids[:128]+ids[-64:])

    def test_offsets_missing_corrupt_or_not_monotone_fall_back_without_rewriting_input(self):
        text='background '*35+'`FrameMux.flush_batch` '+'environment '*35;ids,offsets=encoded(text)
        wrong=[None,offsets[:-1],[(True,1)]*len(ids),[(2,1)]*len(ids),offsets[:200]+[(0,1)]+offsets[201:]]
        for value in wrong:self.assertEqual(anchors.preserve_query_anchors(text,ids,value),ids[:128]+ids[-64:])

    def test_many_middle_anchors_stay_bounded_and_in_original_order(self):
        text='background '*35+' '.join('function_name_'+str(i)+'()' for i in range(200))+' environment '*35
        result=selected(text);self.assertLessEqual(len(result),192)
        self.assertEqual(result,sorted(set(result)))
        self.assertLessEqual(len(anchors.anchor_spans(text)),32)

    def test_oversized_identifier_is_not_partially_inserted(self):
        text='background '*35+'`'+('very_long_identifier_'*5)+'` '+'environment '*35;ids,_=encoded(text)
        self.assertEqual(selected(text),ids[:128]+ids[-64:])

    def test_parent_visible_anchors_do_not_trigger_a_different_view(self):
        text='PacketLedger.commit_batch() '+('ordinary background '*50)+' final_notes()';ids,_=encoded(text)
        self.assertEqual(selected(text),ids[:128]+ids[-64:])

    def test_middle_sentence_and_following_constraint_are_kept_together(self):
        body='Call FrameMux.flush_batch. Keep failed leases intact. '
        text='ordinary background report. '*30+body+'environment checklist. '*30
        ids,_=encoded(text);result=set(selected(text))
        for phrase in ['FrameMux.flush_batch','Keep failed leases intact']:
            start=text.index(phrase);self.assertTrue(set(ids[start:start+len(phrase)])<=result)

    def test_source_local_view_does_not_refill_with_the_irrelevant_tail(self):
        text='ordinary background report. '*30+'Call FrameMux.flush_batch. Keep failed leases intact. '+'environment checklist. '*30
        ids,_=encoded(text);result=selected(text)
        self.assertLess(len(result),192)
        self.assertFalse(set(result)&set(ids[-60:]))

    def test_a_concise_natural_title_remains_with_a_middle_region(self):
        title='Restore idle lease ownership'
        text=title+'\n'+'ordinary background report. '*30+'Call FrameMux.flush_batch. Keep failed leases intact. '+'environment checklist. '*30
        ids,_=encoded(text);self.assertTrue(set(ids[:len(title)])<=set(selected(text)))

    def test_parent_visible_technical_name_is_retained_when_middle_context_is_added(self):
        text='background '*8+'PacketLedger.flush_batch() '+('ordinary background report. '*30)+'Call FrameMux.flush_batch. Keep failed leases intact. '+'environment checklist. '*30
        ids,_=encoded(text);start=text.index('PacketLedger.flush_batch');self.assertTrue(start<128)
        self.assertTrue(set(ids[start:start+len('PacketLedger.flush_batch')])<=set(selected(text)))

    def test_a_long_irrelevant_prefix_is_not_kept_just_to_fill_a_quota(self):
        text='ordinary background report. '*30+'Call FrameMux.flush_batch. Keep failed leases intact. '+'environment checklist. '*30
        ids,_=encoded(text);self.assertFalse(set(ids[:32])&set(selected(text)))

    def test_input_token_ids_offsets_and_text_are_unchanged(self):
        text='background '*35+'`FrameMux.flush_batch` '+'environment '*35;ids,offsets=encoded(text)
        original_ids=list(ids);original_offsets=list(offsets);anchors.preserve_query_anchors(text,ids,offsets)
        self.assertEqual(ids,original_ids);self.assertEqual(offsets,original_offsets)

class Encoding:
    def __init__(self,text):self.ids,self.offsets=encoded(text)
class Tokenizer:
    def encode(self,text,add_special_tokens=False):return Encoding(text)
class Logits:
    def __init__(self,count):self.values=[0.]*count
    def reshape(self,*args):return self
    def __len__(self):return len(self.values)
    def __iter__(self):return iter(self.values)
    def max(self):return max(self.values)
class ArrayTools:
    int64=int
    @staticmethod
    def asarray(value,dtype=None):return value
    @staticmethod
    def isfinite(value):return [True for _ in value]
    @staticmethod
    def all(value):return all(value)
class Session:
    def __init__(self):self.calls=[]
    def run(self,names,feeds):
        self.calls.append(feeds)
        return [Logits(len(feeds['input_ids']))]

class BoundIntegrationTests(unittest.TestCase):
    def scorer(self):
        obj=reranker.PairScorer.__new__(reranker.PairScorer)
        obj.tokenizer=Tokenizer();obj.session=Session();obj.np=ArrayTools()
        obj.cls=1;obj.sep=2;obj.pad=0;obj.inputs={'input_ids','attention_mask','token_type_ids'}
        obj.lock=threading.Lock();return obj

    def test_actual_scorer_keeps_middle_evidence_with_the_same_16_by3_inference_bound(self):
        obj=self.scorer();query='background '*35+'`FrameMux.flush_batch` '+'environment '*35
        values=obj.scores(query,['original evidence '*100]*16)
        self.assertEqual(values,[0.]*16);self.assertEqual(len(obj.session.calls),16)
        pairs=[row for call in obj.session.calls for row in call['input_ids']]
        self.assertEqual(len(pairs),48);self.assertTrue(all(len(row)<=512 for row in pairs))
        first=obj.session.calls[0];types=first['token_type_ids'][0];ids=first['input_ids'][0]
        query_ids=ids[1:types.index(1)-1];self.assertLessEqual(len(query_ids),192)
        start=query.index('FrameMux.flush_batch');self.assertTrue(set(range(1000+start,1000+start+len('FrameMux.flush_batch')))<=set(query_ids))

    def test_more_than_sixteen_documents_still_refused_before_any_model_call(self):
        obj=self.scorer()
        with self.assertRaises(RuntimeError):obj.scores('owned input',['owned']*17)
        self.assertEqual(obj.session.calls,[])

if __name__=='__main__':unittest.main()

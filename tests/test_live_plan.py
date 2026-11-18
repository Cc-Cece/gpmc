import unittest
import tempfile
from pathlib import Path
from gpmc.live_plan import plan_live, freeze_pair_route
from gpmc.exceptions import ProtocolError


class LivePlanTests(unittest.TestCase):
    def source(self, id, video=False, **kw):
        if "identifier" in kw:
            kw["identifier"]={"same":"11111111-1111-1111-1111-111111111111","pair":"11111111-1111-1111-1111-111111111111","one":"11111111-1111-1111-1111-111111111111","two":"22222222-2222-2222-2222-222222222222"}.get(kw["identifier"],kw["identifier"])
        return {"id": id, "video": video, "size": 100, **kw}
    def test_mixed_library_is_decided_per_file_not_platform(self):
        photo = plan_live({"resources": [self.source("p")]})
        video = plan_live({"resources": [self.source("v", True)]})
        motion = plan_live({"resources": [self.source("m", embedded_motion=True)]})
        pair = plan_live({"resources": [self.source("a", identifier="same"), self.source("b", True, identifier="same")]})
        self.assertEqual([photo["kind"], video["kind"], motion["kind"], pair["kind"]], ["photo", "video", "motion_photo", "apple_pair"])
        self.assertEqual(motion["strategy"], "raw")
        self.assertEqual(pair["strategy"], "original")
    def test_names_and_times_cannot_establish_pair(self):
        result = plan_live({"resources": [self.source("a", name="same.jpg", captured=100), self.source("b", True, name="same.mov", captured=100)]})
        self.assertNotEqual(result["kind"], "apple_pair")
    def test_missing_or_ambiguous_parts_keep_files_with_warning(self):
        resource = self.source("a", identifier="pair")
        self.assertEqual(plan_live({"resources": [resource]})["warning"], "LIVE_PAIR_INCOMPLETE")
        self.assertEqual(plan_live({"resources": [resource], "ambiguous": True})["warning"], "LIVE_PAIR_AMBIGUOUS")
        self.assertEqual(plan_live({"resources": [self.source("a", motion_hint=True)]})["warning"], "MOTION_STRUCTURE_UNKNOWN")
    def test_system_library_relationship_supports_edited_pairs_but_not_conflicting_ids(self):
        resources=[self.source("a"),self.source("b",True)]
        self.assertEqual(plan_live({"resources":resources,"system_pair":True})["kind"],"apple_pair")
        resources[0]["identifier"]="11111111-1111-1111-1111-111111111111";resources[1]["identifier"]="22222222-2222-2222-2222-222222222222"
        self.assertNotEqual(plan_live({"resources":resources,"system_pair":True})["kind"],"apple_pair")
    def test_versions_are_not_cross_paired(self):
        resources=[self.source("a",group="original"),self.source("b",True,group="current")]
        self.assertEqual(plan_live({"resources":resources,"system_pair":True})["warning"],"LIVE_PAIR_INCOMPLETE")
    def test_plan_does_not_change_due_to_input_order_or_filenames(self):
        resources=[self.source("a",identifier="pair",sha256="hash-a"),self.source("b",True,identifier="pair",sha256="hash-b")]
        first=plan_live({"resources":resources});resources.reverse()
        self.assertEqual(first,plan_live({"resources":resources}))
        resources[0]["name"]="renamed.mov"
        self.assertEqual(first,plan_live({"resources":resources}))
    def test_manual_override_is_explicit_and_never_automatic_conversion(self):
        resources=[self.source("a",identifier="pair"),self.source("b",True,identifier="pair")]
        for strategy in ["original","stepwise","motion"]:
            self.assertEqual(plan_live({"resources":resources,"strategy":strategy})["strategy"],strategy)
    def test_invalid_inputs_rejected(self):
        for resources in [[],[self.source("a"),self.source("a")],[self.source("a",size=0)]]:
            with self.assertRaises(ProtocolError):plan_live({"resources":resources})
    def test_retry_cannot_change_joint_to_stepwise_after_cloud_state_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"route.json"
            self.assertEqual(freeze_pair_route(path,"photo","video",False),"joint")
            self.assertEqual(freeze_pair_route(path,"photo","video",True),"joint")
            with self.assertRaises(ProtocolError):freeze_pair_route(path,"changed-photo","video",True)
    def test_existing_photo_chooses_stepwise_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"route.json"
            self.assertEqual(freeze_pair_route(path,"photo","video",True),"reconcile")
            self.assertEqual(freeze_pair_route(path,"photo","video",False),"reconcile")
    def test_incomplete_route_checkpoint_never_authorizes_new_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"route.json";path.write_text("{",encoding="utf-8")
            with self.assertRaises(ProtocolError):freeze_pair_route(path,"photo","video",False)

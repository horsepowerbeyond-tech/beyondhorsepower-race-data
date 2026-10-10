import copy
import io
import json
from pathlib import Path
import sys
import os
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"automation"))
from publish_pages import Shopify,protected_body,START,END,race_body,admin_token

class FakeShopify(Shopify):
    def __init__(self,page=None):self.page=page;self.writes=[]
    def find(self,handle):return self.page
    def graphql(self,query,variables):
        self.writes.append(variables)
        field="pageUpdate" if "Update(" in query else "pageCreate"
        return {field:{"page":{"id":"gid://shopify/Page/123","handle":variables["page"]["handle"],"publishedAt":"2026-10-10T00:00:00Z" if variables["page"]["isPublished"] else None},"userErrors":[]}}

class PublisherTests(unittest.TestCase):
    def test_client_credentials_exchange_is_server_side_and_once(self):
        credentials={"SHOPIFY_CLIENT_ID":"example-client","SHOPIFY_CLIENT_SECRET":"example-secret"}
        with patch.dict(os.environ,credentials,clear=True),patch("publish_pages.urllib.request.urlopen",return_value=io.BytesIO(b'{"access_token":"test-token","expires_in":86399}')) as opening:
            self.assertEqual(admin_token("example-store.myshopify.com"),"test-token")
            self.assertEqual(opening.call_count,1)
            request=opening.call_args.args[0]
            self.assertEqual(request.full_url,"https://example-store.myshopify.com/admin/oauth/access_token")
            self.assertIn(b"grant_type=client_credentials",request.data)
    def test_incomplete_client_credentials_are_refused(self):
        with patch.dict(os.environ,{"SHOPIFY_CLIENT_ID":"example-client"},clear=True):
            with self.assertRaises(ValueError):admin_token("example-store.myshopify.com")
    def test_existing_token_connection_is_supported(self):
        with patch.dict(os.environ,{"SHOPIFY_ADMIN_TOKEN":"existing-token"},clear=True):
            self.assertEqual(admin_token("example-store.myshopify.com"),"existing-token")
    def test_token_exchange_rejects_a_foreign_hostname(self):
        with patch("publish_pages.urllib.request.urlopen") as opening:
            with self.assertRaises(ValueError):admin_token("example.invalid")
            opening.assert_not_called()
    def page(self):
        return {"id":"gid://shopify/Page/123","title":"Race","handle":"race-2024-08-monaco-grand-prix","templateSuffix":"race-replay","publishedAt":"2026-10-10T00:00:00Z","body":f"{START}old{END}<p>Original analysis</p>","metafields":{"nodes":[{"key":"race_id","value":"2024-08"}]}}
    def call(self,api,**kwargs):
        return api.upsert("race-2024-08-monaco-grand-prix","Race","New results","race-replay","race_id","2024-08",**kwargs)
    def test_preserve_original_commentary(self):
        updated=protected_body(f"<p>Intro</p>{START}old{END}<p>Editorial</p>","new")
        self.assertEqual(updated,f"<p>Intro</p>{START}\nnew\n{END}<p>Editorial</p>")
    def test_refuse_unmarked_existing_content(self):
        with self.assertRaises(ValueError):protected_body("<p>Manual page</p>","new")
    def test_refuse_foreign_existing_page(self):
        page=self.page();page["metafields"]["nodes"][0]["value"]="another-race"
        api=FakeShopify(page)
        with self.assertRaises(ValueError):self.call(api)
        self.assertEqual(api.writes,[])
    def test_new_pages_are_draft_unless_requested(self):
        api=FakeShopify();page=self.call(api)
        self.assertFalse(api.writes[0]["page"]["isPublished"]);self.assertIsNone(page["publishedAt"])
        api=FakeShopify();self.call(api,publish=True)
        self.assertTrue(api.writes[0]["page"]["isPublished"])
    def test_updates_do_not_unpublish_existing_live_pages(self):
        api=FakeShopify(self.page());self.call(api)
        self.assertTrue(api.writes[0]["page"]["isPublished"])
        self.assertIn("Original analysis",api.writes[0]["page"]["body"])
    def test_unchanged_content_does_not_write(self):
        page=self.page();page["body"]=protected_body("","New results")
        api=FakeShopify(page);self.call(api)
        self.assertEqual(api.writes,[])
    def test_summary_reports_missing_fields_without_inventing_values(self):
        root=Path(__file__).resolve().parents[1]
        data=json.loads((root/"public/races/2024-08.json").read_text())
        body=race_body(data,"/pages/race-centre")
        self.assertIn("Charles Leclerc",body);self.assertIn("1:14.165",body);self.assertIn("Unavailable",body)
        self.assertNotIn("<td>None</td>",body)

if __name__=="__main__":unittest.main()

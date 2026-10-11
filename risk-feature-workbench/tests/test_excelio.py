from pathlib import Path
import tempfile
import unittest

from openpyxl import Workbook,load_workbook
from riskmatch.errors import InputError
from riskmatch.excelio import HEADERS,read_requests,write_results,prepare_requests

class ExcelTests(unittest.TestCase):
    def make(self,path):
        wb=Workbook()
        ws=wb.active
        ws.title="Girdi"
        ws.append(HEADERS)
        ws.append(["Başvuru","A","Numeric","Türkçe<br>bir\niki","Evet","=1+1","3 gün"])
        ws.append(["Başvuru","A","Numeric",None,"Hayır","Kullanıcı görüşü",None])
        ws.append([None]*7)
        ws.append(["Başvuru","Oran","Numeric","=B2/B3",None,None,None])
        wb.create_sheet("İkinci").append(HEADERS)
        wb["İkinci"].append(["Risk","B","Numeric","",None,None,None])
        wb.save(path)
        wb.close()

    def test_preserves_all_values_sheets_order_and_neutralizes_formula(self):
        with tempfile.TemporaryDirectory() as directory:
            src=Path(directory)/"source.xlsx"
            dst=Path(directory)/"out.xlsx"
            self.make(src)
            req=read_requests(src)
            self.assertEqual([r["name"] for r in req],["A","A","Oran","B"])
            self.assertEqual(len({r["row_id"] for r in req}),4)
            self.assertEqual(req[2]["excel_row"],5)
            self.assertEqual(req[2]["description_for_parse"],"A / A")
            rows=[{"row_id":r["row_id"],"physical_name":"unknown","semantic_status":"insufficient","production_status":"insufficient","reasons":[],"errors":[],"candidates":[]} for r in req]
            write_results(src,dst,req,rows,{"Mod":"DEMO"})
            wb=load_workbook(dst,data_only=False)
            self.assertEqual(wb.sheetnames[:3],["Sonuçlar","Adaylar","Çalışma bilgisi"])
            self.assertEqual(wb["Girdi 1 Girdi"]["D2"].value,"Türkçe<br>bir\niki")
            self.assertEqual(wb["Girdi 1 Girdi"]["F2"].value,"=1+1")
            self.assertEqual(wb["Girdi 1 Girdi"]["F2"].data_type,"s")
            self.assertEqual(wb["Girdi 1 Girdi"]["G2"].value,"3 gün")
            self.assertIsNone(wb["Girdi 1 Girdi"]["B4"].value)
            self.assertEqual(wb["Sonuçlar"].max_row,5)
            wb.close()

    def test_ids_persist_through_edit_of_prepared_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            src=Path(directory)/"source.xlsx"
            identified=Path(directory)/"identified.xlsx"
            self.make(src)
            before=read_requests(src)
            prepare_requests(src,identified,before)
            wb=load_workbook(identified)
            wb["Girdi"]["D2"]="Düzeltilmiş açıklama"
            wb.save(identified)
            wb.close()
            after=read_requests(identified)
            self.assertEqual([r["row_id"] for r in before],[r["row_id"] for r in after])

    def test_unrecognized_sheet_and_formula_exposed(self):
        with tempfile.TemporaryDirectory() as directory:
            src=Path(directory)/"in.xlsx"
            self.make(src)
            wb=load_workbook(src)
            wb["Girdi"]["D5"]="=WEBSERVICE(\"http://never.invalid\")"
            wb.create_sheet("Bozuk").append(["Alanları içeriyor ama başlık yok"])
            wb.save(src)
            wb.close()
            req=read_requests(src)
            self.assertTrue(req[2]["input_errors"])
            self.assertTrue(req[-1]["input_errors"])
            with self.assertRaises(InputError):prepare_requests(src,src)

if __name__=="__main__":unittest.main()

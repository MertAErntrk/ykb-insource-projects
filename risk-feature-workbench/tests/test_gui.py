from pathlib import Path
from queue import Empty
import tempfile
import unittest
from unittest.mock import patch

class GuiTests(unittest.TestCase):
    def test_worker_demo_and_error_paths_without_display(self):
        from riskmatch.gui import App
        from queue import Queue
        app=object.__new__(App)
        app.queue=Queue()
        sample=Path(__file__).resolve().parents[1]/"examples/sentetik_talepler.xlsx"
        with tempfile.TemporaryDirectory() as directory:
            app.worker(str(sample),str(Path(directory)/"out.xlsx"),"demo",None)
            ok,result=app.queue.get_nowait()
            self.assertTrue(ok,result)
            self.assertEqual(len(result["results"]),28)
            app.worker(str(sample),str(Path(directory)/"out.xlsx"),"real",None)
            ok,error=app.queue.get_nowait()
            self.assertFalse(ok)
            self.assertIn("config",error)

if __name__=="__main__":unittest.main()

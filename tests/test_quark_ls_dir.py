import unittest
from unittest.mock import patch

from quark_auto_save import Quark


class QuarkLsDirTests(unittest.TestCase):
    def test_safe_ls_dir_normalizes_error_dict_to_list(self):
        account = Quark("kps=x; sign=y; vcode=z", 0)
        with patch.object(account, "ls_dir", return_value={"error": "request error"}):
            self.assertEqual([], account._safe_ls_dir("fid"))

    def test_safe_ls_dir_keeps_normal_list(self):
        account = Quark("kps=x; sign=y; vcode=z", 0)
        expected = [{"fid": "1", "file_name": "episode.mp4"}]
        with patch.object(account, "ls_dir", return_value=expected):
            self.assertEqual(expected, account._safe_ls_dir("fid"))


if __name__ == "__main__":
    unittest.main()

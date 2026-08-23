import unittest

from app.ui.vertical_resize_grip import compute_resized_height


class TestComputeResizedHeight(unittest.TestCase):
    def test_drag_down_grows_height(self):
        self.assertEqual(compute_resized_height(160, 40, 80, 640), 200)

    def test_drag_up_shrinks_height(self):
        self.assertEqual(compute_resized_height(160, -40, 80, 640), 120)

    def test_clamped_to_minimum(self):
        self.assertEqual(compute_resized_height(100, -500, 80, 640), 80)

    def test_clamped_to_maximum(self):
        self.assertEqual(compute_resized_height(600, 500, 80, 640), 640)

    def test_zero_delta_is_a_noop(self):
        self.assertEqual(compute_resized_height(200, 0, 80, 640), 200)


if __name__ == "__main__":
    unittest.main()

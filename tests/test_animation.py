from pathlib import Path
import tempfile
import unittest
import zipfile

import numpy as np
from PIL import Image

from app.animation_decode import iter_pillow_frames, iter_webm_frames
from app.archive import ComicArchive
from app.compressed_collection import CompressedComicCollection


class AnimationTests(unittest.TestCase):
    def test_gif_streams_frames_without_preloading_collection(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            gif = root / "anim.gif"
            frames = [
                Image.new("RGB", (48, 40), (255, 0, 0)),
                Image.new("RGB", (48, 40), (0, 255, 0)),
                Image.new("RGB", (48, 40), (0, 0, 255)),
            ]
            frames[0].save(
                gif, save_all=True, append_images=frames[1:], loop=0,
                duration=[40, 60, 80], format="GIF"
            )
            decoded = list(iter_pillow_frames(str(gif), max_dim=32))
            self.assertEqual(len(decoded), 3)
            self.assertEqual([d for _, d in decoded], [40, 60, 80])
            self.assertTrue(all(max(frame.size) <= 32 for frame, _ in decoded))

            arc = ComicArchive(root)
            try:
                self.assertEqual(arc.count(), 1)
                self.assertTrue(arc.animation_candidate(0))
                source = arc.animation_source(0)
                self.assertEqual(Path(source["path"]), gif)
            finally:
                arc.close()

    def test_static_png_is_only_a_candidate_and_stays_one_frame(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "still.png"
            Image.new("RGB", (30, 20), "white").save(path)
            arc = ComicArchive(path)
            try:
                # O teste é propositalmente barato/O(1); o worker descobre que
                # há só um quadro sem bloquear a abertura da UI.
                self.assertTrue(arc.animation_candidate(0))
                self.assertEqual(list(iter_pillow_frames(str(path))), [])
            finally:
                arc.close()



    def test_large_animated_folder_is_indexed_without_decoding_frames(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Arquivos podem até ser vazios: abrir a pasta deve indexar nomes,
            # nunca tocar no decoder até uma página realmente ser exibida.
            for i in range(1500):
                (root / f"anim_{i:05d}.gif").touch()
            with patch("app.archive.Image.open", side_effect=AssertionError("decoder chamado cedo")):
                arc = ComicArchive(root)
                try:
                    self.assertEqual(arc.count(), 1500)
                    self.assertTrue(arc.animation_candidate(0))
                finally:
                    arc.close()

    def test_animated_image_inside_outer_zip_remains_animated(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            gif = root / "animated.gif"
            a = Image.new("RGB", (32, 32), "red")
            b = Image.new("RGB", (32, 32), "green")
            a.save(gif, save_all=True, append_images=[b], duration=[50, 70], loop=0)

            outer = root / "gallery.zip"
            with zipfile.ZipFile(outer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.write(gif, "images/001.gif")

            collection = CompressedComicCollection(outer)
            try:
                self.assertEqual(collection.count(), 1)
                self.assertEqual(collection.member_kind(0), "image")
                arc = ComicArchive(collection.member_path(0))
                try:
                    self.assertTrue(arc.animation_candidate(0))
                    source = arc.animation_source(0)
                    self.assertIn("path", source)
                    frames = list(iter_pillow_frames(source["path"]))
                    self.assertEqual(len(frames), 2)
                finally:
                    arc.close()
            finally:
                collection.close()

    def test_animated_pages_inside_cbz_are_lazy_sources(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            gif = root / "inside.gif"
            a = Image.new("RGB", (32, 32), "red")
            b = Image.new("RGB", (32, 32), "blue")
            a.save(gif, save_all=True, append_images=[b], duration=50, loop=0)
            cbz = root / "animated.cbz"
            with zipfile.ZipFile(cbz, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.write(gif, "pages/001.gif")
            arc = ComicArchive(cbz)
            try:
                self.assertEqual(arc.count(), 1)
                self.assertTrue(arc.animation_candidate(0))
                source = arc.animation_source(0)
                self.assertIn("data", source)
                frames = list(iter_pillow_frames(__import__("io").BytesIO(source["data"])))
                self.assertEqual(len(frames), 2)
            finally:
                arc.close()

    def test_webm_in_image_folder_uses_first_frame_and_streams(self):
        try:
            import imageio.v2 as imageio
            import imageio_ffmpeg  # noqa: F401
        except Exception:
            self.skipTest("imageio-ffmpeg não disponível")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            webm = root / "clip.webm"
            arrays = []
            for value in (20, 100, 220):
                arr = np.zeros((64, 64, 3), dtype=np.uint8)
                arr[..., 0] = value
                arrays.append(arr)
            imageio.mimsave(str(webm), arrays, fps=5, codec="libvpx-vp9")

            arc = ComicArchive(root)
            try:
                self.assertEqual(arc.count(), 1)
                self.assertEqual(arc.page_suffix(0), ".webm")
                self.assertTrue(arc.animation_candidate(0))
                first = arc.load_image(0, max_dim=48)
                self.assertLessEqual(max(first.size), 48)
                streamed = []
                for frame, delay in iter_webm_frames(webm, max_dim=40):
                    streamed.append((frame.size, delay))
                    if len(streamed) == 3:
                        break
                self.assertEqual(len(streamed), 3)
                self.assertTrue(all(max(size) <= 40 for size, _ in streamed))
                self.assertTrue(all(delay >= 20 for _, delay in streamed))
            finally:
                arc.close()


if __name__ == "__main__":
    unittest.main()

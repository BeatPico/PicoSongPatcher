"""PyInstaller runtime hook: the public build uses the local PCM decoder."""
from native_audio import install
install()

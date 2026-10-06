import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "export_netlist.py"


def load_script():
    spec = importlib.util.spec_from_file_location("export_netlist", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_uses_env_var(tmp_path, monkeypatch):
    mod = load_script()
    fake = tmp_path / "kicad-cli.exe"
    fake.write_text("")
    monkeypatch.setenv("KICAD_CLI", str(fake))
    assert mod.find_kicad_cli() == str(fake)


def test_env_var_pointing_nowhere(tmp_path, monkeypatch):
    mod = load_script()
    monkeypatch.setenv("KICAD_CLI", str(tmp_path / "nope.exe"))
    assert mod.find_kicad_cli() is None


def test_picks_newest_windows_install(tmp_path, monkeypatch):
    mod = load_script()
    monkeypatch.delenv("KICAD_CLI", raising=False)
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    for version in ("8.0", "10.0"):
        folder = tmp_path / "KiCad" / version / "bin"
        folder.mkdir(parents=True)
        (folder / "kicad-cli.exe").write_text("")
    expected = tmp_path / "KiCad" / "10.0" / "bin" / "kicad-cli.exe"
    assert mod.find_kicad_cli() == str(expected)
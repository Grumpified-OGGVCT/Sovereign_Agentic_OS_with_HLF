from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_visual_brief_assets_exist_with_accessible_titles() -> None:
    brief = REPO_ROOT / "docs" / "sovereign_visual_brief.svg"
    storyboard = REPO_ROOT / "docs" / "hlf_execution_storyboard.svg"

    assert brief.exists()
    assert storyboard.exists()
    assert "<title" in brief.read_text(encoding="utf-8")
    assert "<title" in storyboard.read_text(encoding="utf-8")


def test_readme_and_demo_reference_new_visuals() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    demo = (REPO_ROOT / "docs" / "index.html").read_text(encoding="utf-8")

    assert "docs/sovereign_visual_brief.svg" in readme
    assert "docs/hlf_execution_storyboard.svg" in readme
    assert "docs/system_architecture.png" in readme
    assert "docs/registry_router_flow.png" in readme
    assert "docs/jules_governance_pipeline.png" in readme
    assert "visual-brief-title" in demo
    assert 'src="sovereign_visual_brief.svg"' in demo
    assert 'src="hlf_execution_storyboard.svg"' in demo
    assert 'src="system_architecture.png"' in demo
    assert 'src="registry_router_flow.png"' in demo
    assert 'src="jules_governance_pipeline.png"' in demo

from app.asset_catalog import AssetCatalogService
from app.feishu_sync import FeishuSyncService
from app.jimeng_operator import JimengOperator
from app.openclaw import OpenClawService
from app.prompt_cache import PromptCacheService
from app.video_analyzer import VideoAnalyzerService


def test_service_imports_and_instantiation() -> None:
    assert isinstance(FeishuSyncService(), FeishuSyncService)
    assert isinstance(AssetCatalogService(), AssetCatalogService)
    assert isinstance(OpenClawService(), OpenClawService)
    assert isinstance(JimengOperator(), JimengOperator)
    assert isinstance(VideoAnalyzerService(), VideoAnalyzerService)
    assert isinstance(PromptCacheService(), PromptCacheService)

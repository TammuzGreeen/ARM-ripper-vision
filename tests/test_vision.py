import pytest
from arm_ripper_vision.vision import DisabledVisionProvider
@pytest.mark.asyncio
async def test_vision_disabled_in_v1():
    v=DisabledVisionProvider()
    assert (await v.status())["enabled"] is False
    assert await v.observe()==[]

import httpx
import logging
import asyncio

logger = logging.getLogger(__name__)

async def safe_http_request(
    http_client,
    method: str, 
    url: str, 
    headers: dict = None, 
    data: dict = None,
    json: dict = None, 
    params: dict = None, 
    follow_redirects: bool = True,
    timeout: float = None
):
    #method는 GET, POST, PATCH, DELETE
    method = method.upper()
    current_loop = asyncio.get_running_loop()
    
    is_same_loop = (
        http_client is not None 
        and hasattr(http_client, "_transport") 
        and getattr(http_client._transport, "_loop", None) == current_loop
    )
    if is_same_loop and not http_client.is_closed:
        try:
            return await http_client.request(
                method=method, url=url, headers=headers, data=data, json=json, params=params, follow_redirects=follow_redirects, timeout=timeout
            )
        except RuntimeError as e:
            logger.warning(f"임시 HTTP 클라이언트로 전환: {e}",exc_info=True)
        except Exception as e:
            logger.warning(f"임시 HTTP 클라이언트로 전환: {e}",exc_info=True)

    limits = httpx.Limits(max_keepalive_connections=2, max_connections=10)
    timeout = httpx.Timeout(20.0, connect=10.0)
    
    async with httpx.AsyncClient(limits=limits, timeout=timeout, http2=True) as temp_client:
        
        return await temp_client.request(
            method=method, url=url, headers=headers, data=data, json=json, params=params, follow_redirects=follow_redirects, timeout=timeout
        )
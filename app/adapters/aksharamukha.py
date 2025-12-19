import logging
try:
    from aksharamukha.transliterate import process
except ImportError:
    process = None


class AksharaAdapter:
    async def transliterate(self, text: str, mode: str):
        if process is None:
            logging.error("[AKSHARA] library missing")
            return []
        # FastAPI is async; aksharamukha is sync. Call directly (cheap) for now.
        output = process("ISO", "Tamil", text)
        if not output:
            return []
        return [output]


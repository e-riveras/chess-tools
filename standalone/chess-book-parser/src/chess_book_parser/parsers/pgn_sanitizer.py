import re

class PGNSanitizer:
    @staticmethod
    def sanitize(pgn_text: str) -> str:
        """
        Sanitize PGN text to ensure consistent formatting.
        - Adds space after move numbers (1.e4 -> 1. e4)
        - Adds space after variation black move numbers (1...e5 -> 1... e5)
        - Ensures space before move numbers
        """
        # 1. Add space after single dot move numbers: "1.e4" -> "1. e4"
        # Look for digit + dot + non-space
        pgn_text = re.sub(r'(\d+\.)([^\s\.])', r'\1 \2', pgn_text)

        # 2. Add space after triple dot move numbers: "1...e5" -> "1... e5"
        # Look for digit + ... + non-space
        pgn_text = re.sub(r'(\d+\.\.\.)([^\s])', r'\1 \2', pgn_text)

        return pgn_text

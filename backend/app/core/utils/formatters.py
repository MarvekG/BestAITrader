import re


class StockCodeStandardizer:
    @staticmethod
    def standardize(code: str) -> str:
        """
        将股票代码标准化为 '数字.市场' 格式 (例如 002142.SZ)
        支持输入: 002142, 002142.SZ, sz002142, SH600000等
        """
        if code is None:
            return None

        # Ensure string type for numeric inputs (e.g. from DataFrame/JSON)
        if not isinstance(code, str):
            code = str(code)

        if not code:
            return code

        code = code.strip().upper()

        # 1. 处理已经带市场后缀的情况 (数字.市场)
        if '.' in code:
            parts = code.split('.')
            if len(parts) == 2 and parts[1] in ['SH', 'SZ', 'BJ', 'HK']:
                # 检查数字部分是否需要补全前导零 (通常沪深京是6位)
                if parts[1] in ['SH', 'SZ', 'BJ'] and len(parts[0]) < 6:
                    parts[0] = parts[0].zfill(6)
                return f"{parts[0]}.{parts[1]}"

        # 2. 处理前缀格式 (sz002142, sh600000)
        prefix_match = re.search(r'^(SH|SZ|BJ|HK)(\d+)$', code)
        if prefix_match:
            market = prefix_match.group(1)
            num = prefix_match.group(2)
            if market in ['SH', 'SZ', 'BJ'] and len(num) < 6:
                num = num.zfill(6)
            return f"{num}.{market}"

        # 3. 只有纯数字的情况，进行推断
        num_match = re.search(r'^(\d+)$', code)
        if num_match:
            num = num_match.group(1)
            if len(num) < 6:
                num = num.zfill(6)

            # 推断逻辑
            if num.startswith(('60', '68', '900', '730', '780')):
                return f"{num}.SH"
            elif num.startswith(('50', '51', '52', '56', '58')):  # 上交所 ETF/基金
                return f"{num}.SH"
            elif num.startswith(('00', '30', '200', '080')):
                return f"{num}.SZ"
            elif num.startswith(('15', '16', '18')):  # 深交所 ETF/基金
                return f"{num}.SZ"
            elif num.startswith(('43', '83', '87', '88', '92', '81')):
                return f"{num}.BJ"
            elif len(num) == 5:  # 港股通常是5位
                return f"{num}.HK"

        return code

    @staticmethod
    def get_market(code: str) -> str:
        """获取市场代码 (SH, SZ, BJ等)"""
        std_code = StockCodeStandardizer.standardize(code)
        if '.' in std_code:
            return std_code.split('.')[1]
        return "UNKNOWN"

    @staticmethod
    def to_number(code: str) -> str:
        """
        转换为 6 位纯数字股票代码。

        Args:
            code: 原始股票代码。

        Returns:
            去除市场后缀后的数字股票代码。
        """
        std_code = StockCodeStandardizer.standardize(code)
        if '.' in std_code:
            return std_code.split('.')[0]
        return code

    @staticmethod
    def to_standard(code: str) -> str:
        """转换为 Tushare 需要的格式 (数字.市场，例如 600519.SH)"""
        return StockCodeStandardizer.standardize(code)

    @staticmethod
    def to_standard_index(code: str) -> str:
        """
        转换为 Tushare 指数接口需要的格式 (数字.市场，例如 000001.SH)
        特别处理指数代码规则，避免与个股规则冲突
        """
        if code is None:
            return None

        code = str(code).strip().upper()

        # 1. 已经包含后缀的不做处理
        if '.' in code:
            parts = code.split('.')
            if len(parts) == 2 and parts[1] in ['SH', 'SZ', 'BJ', 'HK']:
                return code

        # 2. 只有纯数字的情况，针对指数规则推断
        # 上证指数 000xxx -> .SH
        # 深证/创业板指数 399xxx -> .SZ
        if code.isdigit():
            if code.startswith('000'):
                return f"{code}.SH"
            elif code.startswith('399'):
                return f"{code}.SZ"

        # 3. 其他情况保持原样返回
        # 不调用 standardize() 以免混用个股规则
        return code

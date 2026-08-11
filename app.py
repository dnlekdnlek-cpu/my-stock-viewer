import streamlit as st
import pandas as pd
import numpy as np
import FinanceDataReader as fdr
from pykrx import stock
from datetime import datetime, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time
import requests

# =========================================================
# pykrx 헤더 패치 (KRX 서버가 클라우드 요청을 봇으로 오인해 403 반환하는 문제 완화)
# =========================================================
def patch_pykrx_headers():
    try:
        from pykrx.website.comm.webio import Get
        original_init = Get.__init__

        def patched_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            self.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/124.0.0.0 Safari/537.36",
                "Referer": "https://data.krx.co.kr/"
            })
        Get.__init__ = patched_init
    except Exception as e:
        print(f"pykrx 헤더 패치 실패: {e}")

patch_pykrx_headers()


# =========================================================
# 기본 설정
# =========================================================
st.set_page_config(page_title="주식 지표 분석기", layout="wide")

MARKET = "ALL"  # KOSPI+KOSDAQ 전체 대상


# =========================================================
# 공통 유틸 함수
# =========================================================
@st.cache_data(ttl=3600)
def get_krx_ticker_list():
    """KRX 종목명-코드 매핑 테이블"""
    df = fdr.StockListing("KRX")
    df = df[["Code", "Name"]].dropna()
    return df


def fetch_with_retry(fetch_func, max_retry=3, delay=1.5):
    """
    일시적인 403/타임아웃에 대응하기 위해 짧은 대기 후 재시도.
    """
    for attempt in range(max_retry):
        try:
            df = fetch_func()
            if df is not None and not df.empty:
                return df
        except Exception as e:
            print(f"시도 {attempt+1}/{max_retry} 실패: {e}")
        time.sleep(delay)
    return pd.DataFrame()


def get_latest_valid_date(fetch_func, max_lookback=10):
    """
    fetch_func: 특정 날짜(YYYYMMDD)를 인자로 받아 DataFrame을 반환하는 함수
    빈 DataFrame이 아닌 결과가 나올 때까지 하루씩 뒤로 조회 + 재시도 결합
    """
    for i in range(max_lookback):
        target_date = (datetime.today() - timedelta(days=i)).strftime("%Y%m%d")
        df = fetch_with_retry(lambda: fetch_func(target_date))
        if not df.empty:
            return df, target_date
    return pd.DataFrame(), None


# =========================================================
# 네이버 금융 폴백 (pykrx가 완전히 막혔을 때 최종 안전망)
# =========================================================
def fetch_fundamental_naver_fallback(ticker_code):
    try:
        url = f"https://finance.naver.com/item/main.naver?code={ticker_code}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        res = requests.get(url, headers=headers, timeout=5)
        res.encoding = "euc-kr"
        tables = pd.read_html(res.text)

        # 네이버 페이지 구조상 PER/PBR 정보가 포함된 테이블 탐색
        for t in tables:
            cols = [str(c) for c in t.columns]
            if any("PER" in c for c in cols) or (t.astype(str).apply(lambda x: x.str.contains("PER", na=False)).any().any()):
                return t
        return None
    except Exception as e:
        print(f"네이버 폴백 실패: {e}")
        return None


# =========================================================
# 인기 종목 TOP 10 (거래대금 기준)
# =========================================================
@st.cache_data(ttl=1800)
def fetch_top10():
    def _fetch(date):
        return stock.get_market_ohlcv_by_ticker(date, market=MARKET)

    df, used_date = get_latest_valid_date(_fetch)
    if df.empty:
        return pd.DataFrame(), None

    df = df.sort_values("거래대금", ascending=False).head(10).reset_index()
    df = df.rename(columns={"티커": "Code"})

    name_map = get_krx_ticker_list().set_index("Code")["Name"]
    df["종목명"] = df["Code"].map(name_map)
    return df[["Code", "종목명", "종가", "등락률", "거래대금"]], used_date


# =========================================================
# 투자 지표 (PER, PBR, EPS, 배당수익률)
# =========================================================
@st.cache_data(ttl=1800)
def fetch_fundamental(ticker_code):
    def _fetch(date):
        return stock.get_market_fundamental_by_ticker(date, market=MARKET)

    df, used_date = get_latest_valid_date(_fetch)
    if not df.empty and ticker_code in df.index:
        return df.loc[ticker_code], used_date, "pykrx"

    # pykrx가 계속 실패하면 네이버 폴백 시도
    fallback = fetch_fundamental_naver_fallback(ticker_code)
    if fallback is not None:
        return fallback, "실시간(네이버)", "naver"

    return None, None, None


# =========================================================
# 기술적 지표 계산 (MA, RSI, MACD)
# =========================================================
def calc_indicators(df):
    df = df.copy()
    df["MA5"] = df["Close"].rolling(5).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()

    delta = df["Close"].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss
    df["RSI"] = 100 - (100 / (1 + rs))

    ema12 = df["Close"].ewm(span=12, adjust=False).mean()
    ema26 = df["Close"].ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["Histogram"] = df["MACD"] - df["Signal"]

    return df


# =========================================================
# 사이드바: 실시간 인기 종목 TOP 10
# =========================================================
with st.sidebar:
    st.markdown("### 🔥 실시간 인기 종목 TOP 10")
    st.caption("최근 거래일 거래대금 기준")

    top10_df, top10_date = fetch_top10()

    if top10_df.empty:
        st.info("최근 10일간 조회 가능한 데이터가 없습니다.")
    else:
        st.caption(f"✅ {top10_date} 기준")
        for _, row in top10_df.iterrows():
            label = f"{row['종목명']} ({row['Code']}) · {row['종가']:,.0f}원 · {row['등락률']:+.2f}%"
            if st.button(label, key=f"top10_{row['Code']}"):
                st.session_state["selected_code"] = row["Code"]
                st.session_state["selected_name"] = row["종목명"]

    st.markdown("---")
    st.warning("⚠️ 본 서비스는 투자 권유가 아닌 재미·참고용입니다. 데이터는 최대 15~30분 지연될 수 있습니다.")


# =========================================================
# 메인 화면: 종목 검색
# =========================================================
st.title("📈 주식 지표 분석기")

ticker_list = get_krx_ticker_list()

search_name = st.text_input(
    "종목명을 입력하세요 (예: 삼성전자)",
    value=st.session_state.get("selected_name", "")
)

selected_code = st.session_state.get("selected_code", None)

if search_name:
    matched = ticker_list[ticker_list["Name"].str.contains(search_name, case=False, na=False)]
    if not matched.empty:
        options = matched["Name"] + " (" + matched["Code"] + ")"
        choice = st.selectbox("검색 결과에서 종목을 선택하세요", options)
        if choice:
            selected_code = choice.split("(")[-1].replace(")", "")
            selected_name = choice.split(" (")[0]
            st.session_state["selected_code"] = selected_code
            st.session_state["selected_name"] = selected_name
    else:
        st.warning("검색 결과가 없습니다.")


# =========================================================
# 선택된 종목 상세 분석
# =========================================================
if selected_code:
    name = st.session_state.get("selected_name", selected_code)
    st.header(f"{name} ({selected_code})")

    end_date = datetime.today()
    start_date = end_date - timedelta(days=365)

    try:
        price_df = fdr.DataReader(selected_code, start_date, end_date)
    except Exception as e:
        price_df = pd.DataFrame()
        st.error(f"가격 데이터를 불러오는 중 오류가 발생했습니다: {e}")

    if price_df.empty:
        st.warning("가격 데이터를 불러올 수 없습니다.")
    else:
        price_df = calc_indicators(price_df)
        latest = price_df.iloc[-1]
        prev = price_df.iloc[-2] if len(price_df) > 1 else latest

        change = latest["Close"] - prev["Close"]
        change_pct = (change / prev["Close"]) * 100 if prev["Close"] else 0

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("현재가", f"{latest['Close']:,.0f} 원", f"{change:+,.0f} ({change_pct:+.2f}%)")
        col2.metric("거래량", f"{latest['Volume']:,.0f}")
        col3.metric("RSI(14)", f"{latest['RSI']:.2f}" if not np.isnan(latest["RSI"]) else "N/A")
        col4.metric("MACD", f"{latest['MACD']:.2f}" if not np.isnan(latest["MACD"]) else "N/A")

        st.markdown("---")

        # ---- 투자 지표 ----
        st.subheader("💰 투자 지표")
        fundamental, fund_date, source = fetch_fundamental(selected_code)

        if fundamental is None:
            st.info("투자 지표 데이터를 불러올 수 없습니다. (휴장일이거나 데이터 미제공 종목)")
        else:
            badge = "pykrx" if source == "pykrx" else "네이버 폴백"
            st.caption(f"✅ {fund_date} 기준 · 출처: {badge}")

            if source == "pykrx":
                fcol1, fcol2, fcol3, fcol4 = st.columns(4)
                fcol1.metric("PER", f"{fundamental.get('PER', 0):.2f}")
                fcol2.metric("PBR", f"{fundamental.get('PBR', 0):.2f}")
                fcol3.metric("EPS", f"{fundamental.get('EPS', 0):,.0f}")
                fcol4.metric("배당수익률", f"{fundamental.get('DIV', 0):.2f}%")
            else:
                st.dataframe(fundamental, use_container_width=True)

        st.markdown("---")

        # ---- 차트 ----
        st.subheader("📊 차트")

        fig = make_subplots(
            rows=3, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.03,
            row_heights=[0.55, 0.2, 0.25],
            subplot_titles=(f"{name} 캔들차트 & 이동평균선", "RSI (14)", "MACD")
        )

        fig.add_trace(go.Candlestick(
            x=price_df.index, open=price_df["Open"], high=price_df["High"],
            low=price_df["Low"], close=price_df["Close"], name="캔들"
        ), row=1, col=1)

        fig.add_trace(go.Scatter(x=price_df.index, y=price_df["MA5"], name="MA5",
                                  line=dict(color="orange", width=1)), row=1, col=1)
        fig.add_trace(go.Scatter(x=price_df.index, y=price_df["MA20"], name="MA20",
                                  line=dict(color="blue", width=1)), row=1, col=1)
        fig.add_trace(go.Scatter(x=price_df.index, y=price_df["MA60"], name="MA60",
                                  line=dict(color="purple", width=1)), row=1, col=1)

        fig.add_trace(go.Scatter(x=price_df.index, y=price_df["RSI"], name="RSI",
                                  line=dict(color="green", width=1)), row=2, col=1)
        fig.add_hline(y=70, line_dash="dash", line_color="red", row=2, col=1)
        fig.add_hline(y=30, line_dash="dash", line_color="blue", row=2, col=1)

        fig.add_trace(go.Bar(x=price_df.index, y=price_df["Histogram"], name="Histogram",
                              marker_color="gray"), row=3, col=1)
        fig.add_trace(go.Scatter(x=price_df.index, y=price_df["MACD"], name="MACD",
                                  line=dict(color="black", width=1)), row=3, col=1)
        fig.add_trace(go.Scatter(x=price_df.index, y=price_df["Signal"], name="Signal",
                                  line=dict(color="red", width=1)), row=3, col=1)

        fig.update_layout(
            height=900,
            xaxis_rangeslider_visible=False,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )

        st.plotly_chart(fig, use_container_width=True)

else:
    st.info("👈 왼쪽 인기 종목을 클릭하거나, 위에서 종목명을 검색해 보세요.")


# =========================================================
# 하단 면책 조항
# =========================================================
st.markdown("---")
st.caption(
    "⚠️ 본 서비스는 투자 권유가 아닌 재미·참고용으로 제작되었습니다. "
    "제공되는 데이터는 실제와 다르거나 지연될 수 있으며, 투자 결정에 대한 책임은 본인에게 있습니다."
)

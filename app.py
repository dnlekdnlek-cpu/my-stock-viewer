import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go

st.set_page_config(page_title="심심풀이 주식 지표 뷰어", page_icon="📊", layout="centered")

st.title("📊 심심풀이 주식 지표 뷰어")
st.caption("투자 조언이 아니라 지표를 구경하는 재미용 사이트예요 🙂")

ticker = st.text_input("종목 코드 입력 (예: AAPL, TSLA, 005930.KS)", "AAPL")
period = st.selectbox("조회 기간", ["3mo", "6mo", "1y", "2y"], index=1)

def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_macd(series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    return macd, signal_line

def fun_comments(rsi_val, macd_val, signal_val, ma5, ma20):
    comments = []
    if pd.notna(rsi_val):
        if rsi_val >= 70:
            comments.append(f"RSI {rsi_val:.1f} — 최근 상승세가 꽤 뜨거웠네요 🔥 (과매수 구간 근처)")
        elif rsi_val <= 30:
            comments.append(f"RSI {rsi_val:.1f} — 최근 좀 눌려있는 모습이에요 🥶 (과매도 구간 근처)")
        else:
            comments.append(f"RSI {rsi_val:.1f} — 특별히 과열되거나 침체된 느낌은 아니에요 😌")
    if pd.notna(macd_val) and pd.notna(signal_val):
        comments.append(
            "MACD가 시그널선 위에 있어요 — 단기 흐름이 위쪽을 보고 있네요 📈"
            if macd_val > signal_val else
            "MACD가 시그널선 아래에 있어요 — 단기 흐름이 아래쪽을 보고 있네요 📉"
        )
    if pd.notna(ma5) and pd.notna(ma20):
        comments.append(
            "5일선이 20일선 위에 있어요 — 단기 모멘텀이 살아있는 편이에요 🐰"
            if ma5 > ma20 else
            "5일선이 20일선 아래에 있어요 — 최근엔 좀 차분한 흐름이네요 🐢"
        )
    return comments

if st.button("분석하기", type="primary") or ticker:
    try:
        df = yf.download(ticker, period=period, progress=False)
        if df.empty:
            st.error("데이터를 찾을 수 없어요. 티커를 확인해주세요.")
        else:
            close = df["Close"]
            df["MA5"] = close.rolling(5).mean()
            df["MA20"] = close.rolling(20).mean()
            df["RSI"] = calc_rsi(close)
            df["MACD"], df["Signal"] = calc_macd(close)

            latest = df.iloc[-1]

            st.subheader(f"{ticker.upper()} 현재가: {float(latest['Close']):.2f}")

            comments = fun_comments(
                float(latest["RSI"]) if pd.notna(latest["RSI"]) else float("nan"),
                float(latest["MACD"]) if pd.notna(latest["MACD"]) else float("nan"),
                float(latest["Signal"]) if pd.notna(latest["Signal"]) else float("nan"),
                float(latest["MA5"]) if pd.notna(latest["MA5"]) else float("nan"),
                float(latest["MA20"]) if pd.notna(latest["MA20"]) else float("nan"),
            )

            for c in comments:
                st.info(c)

            fig = go.Figure()
            fig.add_trace(go.Scatter(x=df.index, y=df["Close"], name="종가", line=dict(color="black", width=1.5)))
            fig.add_trace(go.Scatter(x=df.index, y=df["MA5"], name="5일 이동평균", line=dict(color="#4dabf7", width=1)))
            fig.add_trace(go.Scatter(x=df.index, y=df["MA20"], name="20일 이동평균", line=dict(color="#ff8787", width=1)))
            fig.update_layout(height=400, margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=-0.2))
            st.plotly_chart(fig, use_container_width=True)

            col1, col2 = st.columns(2)
            with col1:
                st.metric("RSI (14)", f"{latest['RSI']:.1f}" if pd.notna(latest['RSI']) else "-")
            with col2:
                st.metric("MACD", f"{latest['MACD']:.2f}" if pd.notna(latest['MACD']) else "-")

    except Exception as e:
        st.error(f"오류가 발생했어요: {e}")

st.markdown("---")
st.caption("⚠️ 이 사이트는 재미로 보는 참고용 지표 요약이며, 투자 권유나 특정 종목의 매수·매도 추천이 아닙니다. 모든 투자 판단과 책임은 본인에게 있습니다.")

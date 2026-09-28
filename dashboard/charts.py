"""
Plotly figure builders shared across tabs.
"""

import pandas as pd
import plotly.express as px


def create_lobbying_treemap(data: pd.DataFrame, year: int):
    """
    Treemap of lobbying spend, grouped by sector → company/ticker.
    Colour intensity reflects spend magnitude (blue → green scale).
    """
    df = data[data["sector"].notna() & (data["total_lobbying_spend"] > 0)].copy()
    df["label"] = df.apply(
        lambda r: r["ticker"]
        if pd.notna(r["ticker"]) and str(r["ticker"]).strip()
        else r["company_name"][:22],
        axis=1,
    )
    df["spend_m"] = (df["total_lobbying_spend"] / 1_000_000).round(2)

    fig = px.treemap(
        df,
        path=["sector", "label"],
        values="total_lobbying_spend",
        color="spend_m",
        color_continuous_scale=[[0, "#1e3a5f"], [0.5, "#60a5fa"], [1.0, "#22c55e"]],
        custom_data=["spend_m"],
    )
    fig.update_traces(
        texttemplate="%{label}<br>$%{customdata[0]:.1f}M",
        hovertemplate="<b>%{label}</b><br>Spend: $%{customdata[0]:.1f}M<extra></extra>",
    )
    fig.update_layout(
        title=f"Lobbying Spend by Sector & Company — {year}",
        plot_bgcolor="#0e1117",
        paper_bgcolor="#0e1117",
        font=dict(color="#ffffff", size=12),
        height=520,
        margin=dict(l=0, r=0, t=45, b=0),
        coloraxis_showscale=False,
    )
    return fig

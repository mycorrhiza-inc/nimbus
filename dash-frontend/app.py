# Import packages
from dash import Dash, html, dash_table, dcc, callback, Output, Input
import pandas as pd
import plotly.express as px
from pathlib import Path
import os

DEFAULT_SCENARIO_FOLDER = os.getenv(
    "DEFAULT_SCENARIO_FOLDER",
    "/home/brad/Documents/energy-democracy/gridmodel/data/switch/scc7a_60_fuel",
)


def build_figure(dir_scenario: Path):
    dir_output = dir_scenario / "outputs"
    dir_input = dir_scenario / "inputs"
    file_buildgen = dir_output / "BuildGen.csv"
    file_geninfo = dir_input / "gen_info.csv"
    buildgen = pd.read_csv(file_buildgen, dtype={})
    buildgen.columns = ["GENERATION_PROJECT", "INVESTMENT_PERIOD", "BuildGen"]
    buildgen["GENERATION_PROJECT"] = buildgen["GENERATION_PROJECT"].str.strip()
    geninfo = pd.read_csv(file_geninfo)
    gen_tech = geninfo[["GENERATION_PROJECT", "gen_tech"]]
    gen_tech["GENERATION_PROJECT"] = gen_tech["GENERATION_PROJECT"].str.strip()
    dumb = buildgen.merge(gen_tech, on="GENERATION_PROJECT", how="left")
    # Generate figure
    fig = px.bar(dumb, y="BuildGen", x="INVESTMENT_PERIOD", color="gen_tech")
    return fig


def start_dash():
    app = Dash()
    fig = build_figure(Path(DEFAULT_SCENARIO_FOLDER))
    # App layout
    app.layout = [
        html.Div(children="Build Capacity"),
        html.Hr(),
        dcc.Graph(figure=fig, id="graph"),
    ]

    # # Add controls to build the interaction
    # @callback(
    #     Output(component_id='controls-and-graph', component_property='figure'),
    #     Input(component_id='controls-and-radio-item', component_property='value')
    # )
    # def update_graph(col_chosen):
    #     fig = px.histogram(df, x='continent', y=col_chosen, histfunc='avg')
    #     return fig

    app.run_server(debug=True, host="0.0.0.0", port=9000)


if __name__ == "__main__":
    start_dash()


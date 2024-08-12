# Import packages
from dash import Dash, html, dash_table, dcc, callback, Output, Input
import pandas as pd
import plotly.graph_objects as go
from pathlib import Path

# Incorporate data
dir_scenario=Path('/home/brad/Documents/energy-democracy/gridmodel/data/switch/scc7a_60_fuel/')
dir_output  = dir_scenario / 'outputs'
dir_input = dir_scenario / 'inputs'
file_buildgen = dir_output / 'BuildGen.csv'
file_geninfo = dir_input / 'gen_info.csv'
buildgen = pd.read_csv(file_buildgen)
buildgen.rename(columns = {'GEN_BLD_YRS_1': 'GENERATION_PROJECT', 'GEN_BLD_YRS_2': 'INVESTMENT_PERIOD'},inplace=True)
geninfo = pd.read_csv(file_geninfo)
buildgen.join(geninfo, on='GENERATION_PROJECT', how = 'left')
# Generate figure

# Initialize the app
app = Dash()

# App layout
app.layout = [
    html.Div(children='My First App with Data, Graph, and Controls'),
    html.Hr(),
    dcc.RadioItems(options=['pop', 'lifeExp', 'gdpPercap'], value='lifeExp', id='controls-and-radio-item'),
    dash_table.DataTable(data=df.to_dict('records'), page_size=6),
    dcc.Graph(figure={}, id='controls-and-graph')
]

# Add controls to build the interaction
@callback(
    Output(component_id='controls-and-graph', component_property='figure'),
    Input(component_id='controls-and-radio-item', component_property='value')
)
def update_graph(col_chosen):
    fig = px.histogram(df, x='continent', y=col_chosen, histfunc='avg')
    return fig

# Run the app
if __name__ == '__main__':
    app.run_server(debug=True, host='0.0.0.0', port=9000)
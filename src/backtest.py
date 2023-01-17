import os
import json
from typing import Dict, List, Optional, Union
from datetime import datetime, timedelta
import numpy as np
from dataclasses import dataclass, field, asdict, make_dataclass
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__file__)

DATE_COLS = ('date',)

def cls_name(self):
    return self.__class__.__name__


def pd_to_native_dtype(dtype):
    """Given a pandas type `dtype`, returns a Python built-in type."""
    if pd.api.types.is_float_dtype(dtype):
        return float
    elif pd.api.types.is_string_dtype(dtype):
        return str
    elif pd.api.types.is_object_dtype(dtype):
        return str
    elif pd.api.types.is_integer_dtype(dtype):
        return int
    elif pd.api.types.is_datetime64_dtype(dtype):
        return np.datetime64
    return None


def infer_date_col(cols: List[str], matches=DATE_COLS) -> Union[str, None]:
    for col_raw in cols:
        col = col_raw.lower().replace(' ', '').strip()
        if col in matches:
            return col_raw
    return None


@dataclass
class FeedBase():
    dt: datetime

    @property
    def df(self):
        return self._get_df()

    def _get_df(self):
        assert getattr(self, '_records'), (
            f"'{cls_name(self)}' has no recorded data"
        )
        df = pd.DataFrame(data=self._records)
        assert 'dt' in df.columns, f"'{cls_name(self)}' has no attribute 'dt'"
        df['dt'] = pd.to_datetime(df['dt'])
        df.set_index('dt', inplace=True)
        df.sort_index(inplace=True)
        return df

    def record(self):
        if not hasattr(self, '_records'):
            self._records = list()
        self._records.append(asdict(self))

    def get_prev(self) -> Dict:
        as_dict = self.df.loc[:self.dt, :].to_dict(orient='records')
        assert as_dict, (
            f"no records before {self.dt} exist in instance "
            f"of {cls_name(self)} (first is {self.df.index[0]})"
        )
        return as_dict[-1]

    def get_next(self) -> Dict:
        as_dict = self.df.loc[self.dt:, :].to_dict(orient='records')
        assert as_dict, (
            f"no records after {self.dt} exist in instance "
            f"of {cls_name(self)} (latest is {self.df.index[-1]})"
        )
        return as_dict[0]

    def set_from_prev(self):
        as_dict: Dict = self.get_prev()
        self._set_from_dict(as_dict)

    def set_from_next(self):
        as_dict: Dict = self.get_next()
        self._set_from_dict(as_dict)

    def _set_from_dict(self, d: Dict):
        for k, v in d.items():
            if not hasattr(self, k):
                logger.warning(f"setting value of attribute {k=} which does "
                               f"not exist in instance of {cls_name(self)}")
            setattr(self, k, v)

    def _record_from_df(self, df: pd.DataFrame):
        date_col = infer_date_col(df.columns)
        if date_col is None:
            logger.warning(f"could not find a date-like column in columns={df.columns}")
        else:
            df.rename(columns={date_col: 'dt'}, inplace=True)

        # Add fields that are not in the current dataclass
        fields_to_add = list()
        for field in df.columns:
            if field not in self.__dataclass_fields__.keys():
                dtype = pd_to_native_dtype(df.dtypes[field])
                fields_to_add.append((field, dtype))
        if fields_to_add:
            field_names = sorted([field[0] for field in fields_to_add])
            cls_name = json.dumps(field_names)
            self.__class__ = make_dataclass(cls_name, fields=fields_to_add, bases=(FeedBase,))

        # Set records from df
        if not hasattr(self, '_records'):
            self._records = list()
        as_dict: List[Dict] = df.to_dict(orient='records')
        if isinstance(as_dict, Dict):
            as_dict = [as_dict]
        self._records.extend(as_dict)
        self._in_df = df

    def record_from_df(self, df):
        self._record_from_df(df.copy())

    def record_from_csv(self, fp: str, **kw):
        df = pd.read_csv(fp, **kw)
        self._record_from_df(df)

    @classmethod
    def from_df(cls, df: pd.DataFrame, *args, name=None, **kw):
        if name is None and hasattr(df, 'name'):
            name = df.name
        feed = cls(*args, dt=np.datetime64(None), name=name, **kw)
        feed.record_from_df(df)
        return feed

    @classmethod
    def from_csv(cls, fp: str, *args, name=None, **kw):
        if name is None:
            name = os.path.basename(os.path.splitext(fp)[0])
        feed = cls(*args, dt=np.datetime64(None), name=name, **kw)
        feed.record_from_csv(fp)
        return feed


class PlotlyPlotter:
    PX_RANGESELECTOR = dict(
        buttons=list([
            dict(count=1, label="1m", step="month", stepmode="backward"),
            dict(count=6, label="6m", step="month", stepmode="backward"),
            dict(count=1, label="YTD", step="year", stepmode="todate"),
            dict(count=1, label="1y", step="year", stepmode="backward"),
            dict(step="all")
        ])
    )

    PX_TICKFORMATSTOPS = [
        dict(dtickrange=[None, 1000], value="%H:%M:%S.%L ms"),
        dict(dtickrange=[1000, 60000], value="%H:%M:%S s"),
        dict(dtickrange=[60000, 3600000], value="%H:%M m"),
        dict(dtickrange=[3600000, 86400000], value="%H:%M h"),
        dict(dtickrange=[86400000, 604800000], value="%e. %b d"),
        dict(dtickrange=[604800000, "M1"], value="%e. %b w"),
        dict(dtickrange=["M1", "M12"], value="%b '%y M"),
        dict(dtickrange=["M12", None], value="%Y Y")
    ]

    def plot(self, *args, exclude_cols=('name', ), **kw):
        return self._plot(in_df=self.df, *args, exclude_cols=('name',), **kw)

    def _plot(
        self, in_df: pd.DataFrame,
        date_col="dt",
        title=None,
        exclude_cols=('name',),
        height=600, width=800,
        labels: Dict = None,
        show: bool = True,
    ):
        df = in_df.reset_index()
        df.drop(columns=list(exclude_cols), errors='ignore', inplace=True)
        fig = px.line(
            df, x=date_col, y=df.columns,
            hover_data={date_col: "|%B %d, %Y"},
            title=title,
            height=height, width=width,
            labels=labels,
        )
        fig.update_xaxes(
            tickformatstops = self.PX_TICKFORMATSTOPS,
            rangeslider_visible=True,
            rangeselector=self.PX_RANGESELECTOR,
        )
        if show:
            fig.show()
        return fig


class StrategyBase(object):

    def __init__(self):
        # super(Opportunity, self).__init__()
        pass

    def step(self):
        raise NotImplementedError(
            f"method 'step' is a virtual method and should be implemented "
            f"in subclass"
        )

class ClockBase(object):

    def __init__(self, dti: pd.DatetimeIndex):
        self.dti = dti
        self.dt = self.dti[0]

    @property
    def name(self) -> Union[str, None]:
        return getattr(self.dti, 'name', None)

    def step(self):
        # TODO: can we next(self.dti) ?
        breakpoint()
        return self.dt


@dataclass
class PriceFeed(FeedBase, PlotlyPlotter):
    price: float = float('nan')
    name: str = 'prices'


class BacktestEngine(object):

    def __init__(
        self, start_date: str, end_date: str, step_size: str = '1D',
        output: Dict[str, str] = None,
        normalize_to_midnight: bool = True,
    ):
        # step_val = int(step_size[:-1])
        # step_unit = step_size[-1]
        # self.step_size = pd.to_timedelta(step_val, step_unit)

        # Containers for feeds and strategies
        self._feeds: Dict[str, FeedBase] = dict()
        self._strats: Dict[str, StrategyBase] = dict()

        # Clocks
        self._clocks: Dict[str, ClockBase] = dict()

        # Define first clock
        self.add_clock(ClockBase(pd.date_range(
            start=start_date, end=end_date,
            normalize=normalize_to_midnight,
            freq=step_size,
        )), name='main')

        # TODO: define output feed

        self.dt = None

    def add_feed(self, feed: FeedBase, name=None):
        if name is None:
            name = getattr(feed, 'name', None)
        if name is None:
            name = cls_name(feed)
        if name in self._feeds:
            raise Exception(f"{cls_name(self)} already has a feed named {name}")
        self._feeds[name] = feed

    def add_strategy(self, strat: StrategyBase, name=None):
        if name is None:
            name = getattr(strat, 'name', None)
        if name is None:
            name = cls_name(strat)
        if name in self._strats:
            raise Exception(f"{cls_name(self)} already has a strategy named {name}")
        self._strats[name] = strat

    def add_clock(self, clock: ClockBase, name=None):
        if name is None:
            name = getattr(clock, 'name', None)
        if name in self._clocks:
            raise Exception(f"{cls_name(self)} already has a clock named {name}")
        self._clocks[name] = clock

    def run(self):
        # Pass all feeds to all strats
        for strat in self._strats.values():
            strat.feeds.extend(self._feeds)

        breakpoint()

    def step(self):
        # Tick main clock
        for clockn, clock in self._clocks.items():
            if clockn == 'main':
                self.dt = clock.step()
            else:
                raise NotImplementedError()

        # Update all feeds
        for feedn, feed in self._feeds.items():
            feed.dt = self.dt
            feed.set_from_prev()

        # Iterate over strategies
        for stratn, strat in self._strats.items():
            strat.step()

        # TODO: record stuff using FeedBase


class BasicStrategy(StrategyBase):

    def step(self):
        if self.feeds['prices'].AAPL < 1.0:
            self.buy(shares=1.)
        elif self.feeds['prices'].AAPL > 2.0:
            self.sell(shares=1.)

        if self.dt >= pd.to_datetime('2022-01-01'):
            self.exit_all()
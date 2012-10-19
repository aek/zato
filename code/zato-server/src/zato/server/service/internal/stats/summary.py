# -*- coding: utf-8 -*-

"""
Copyright (C) 2012 Dariusz Suchojad <dsuch at gefira.pl>

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

# stdlib
import logging
from calendar import monthrange
from copy import deepcopy
from datetime import date, datetime, timedelta
from itertools import chain
from sys import maxint

# dateutil
from dateutil.parser import parse
from dateutil.relativedelta import relativedelta, MO
from dateutil.rrule import DAILY, HOURLY, MINUTELY, MONTHLY, rrule, rruleset, YEARLY

# SciPy
from scipy import stats as sp_stats

# Zato
from zato.common import KVDB, StatsElem, ZatoException
from zato.server.service.internal.stats import BaseAggregatingService, STATS_KEYS, StatsReturningService, \
    stop_excluding_rrset

# ##############################################################################

DEFAULT_STATS = {k:0 for k in STATS_KEYS}
DEFAULT_STATS['mean'] = []
DEFAULT_STATS['min'] = maxint

class DT_PATTERNS(object):
    CURRENT_YEAR_START = '%Y-01-01'
    CURRENT_MONTH_START = '%Y-%m-01'
    CURRENT_DAY_START = '%Y-%m-%d 00:00:00'
    
    CURRENT_HOUR_END = '%Y-%m-%d %H:59:59'
    
    PREVIOUS_HOUR_START = '%Y-%m-%d %H:00:00'
    
    PREVIOUS_YEAR_END = '%Y'
    PREVIOUS_MONTH_END = '%Y-%m'
    PREVIOUS_DAY_END = '%Y-%m-%d 23:59:59'
    
    SUMMARY_SUFFIX_PATTERNS = {
        'by-day': '%Y:%m:%d',
        'by-week': '%Y:%m:%d',
        'by-month': '%Y:%m',
        'by-year': '%Y',
    }
    
# ##############################################################################

class SummarySlice(object):
    """ A convenience wrapper for returning slices a given time range should be
    sliced into when returning summaries across arbitrary time ranges.
    """
    def __init__(self, slice_type, start, stop):
        self.slice_type = slice_type
        self.start = start
        self.stop = stop
        self.total_seconds = (self.stop - self.start).total_seconds()
        
    def __repr__(self):
        return '<{} at {} slice_type:[{}], start:[{}], stop:[{}], total_seconds:[{}]>'.format(
            self.__class__.__name__, hex(id(self)), self.slice_type, self.start.isoformat(), 
                self.stop.isoformat(), self.total_seconds)
    
class SliceStats(object):
    """ A wrapper for combining statistics and how many seconds they represent.
    """
    def __init__(self, slice_type, stats, start, stop, total_seconds):
        self.slice_type = slice_type
        self.start = start
        self.stop = stop
        self.stats = stats
        self.total_seconds = total_seconds
        
    def __repr__(self):
        return '<{} at {} slice_type:[{}], stats:[{}], start:[{}], stop:[{}], total_seconds:[{}]>'.format(
            self.__class__.__name__, hex(id(self)), self.slice_type, self.stats, self.start, self.stop, 
            self.total_seconds)

# ##############################################################################

class BaseSummarizingService(BaseAggregatingService):
    """ Base class for services creating summaries.
    """
    def get_minutely_suffixes(self, now, start=None, stop=None):
        if not start:
            start = parse((now - timedelta(hours=1)).strftime(DT_PATTERNS.PREVIOUS_HOUR_START))
        if not stop:
            stop = parse(now.strftime(DT_PATTERNS.CURRENT_HOUR_END))
            
        return (elem.strftime('%Y:%m:%d:%H:%M') for elem in stop_excluding_rrset(MINUTELY, start, stop))
    
    def get_hourly_suffixes(self, now, start=None, stop=None):
        if not start:
            start = parse(now.strftime(DT_PATTERNS.CURRENT_DAY_START))
        if not stop:
            stop = parse((now - timedelta(hours=2)).strftime(DT_PATTERNS.PREVIOUS_HOUR_START))
        
        return (elem.strftime('%Y:%m:%d:%H') for elem in stop_excluding_rrset(HOURLY, start, stop))
    
    def get_daily_suffixes(self, now, start=None, stop=None):
        if not start:
            start = parse(now.strftime(DT_PATTERNS.CURRENT_MONTH_START))
        if not stop:
            stop = parse((now - timedelta(days=1)).strftime(DT_PATTERNS.PREVIOUS_DAY_END))
        
        return (elem.strftime('%Y:%m:%d') for elem in stop_excluding_rrset(DAILY, start, stop))
    
    def get_monthly_suffixes(self, now, start=None, stop=None):
        if not start:
            start = parse(now.strftime(DT_PATTERNS.CURRENT_YEAR_START))
        if not stop:
            delta = relativedelta(now, months=1)
            stop = parse((start - delta).strftime(DT_PATTERNS.PREVIOUS_MONTH_END))
        
        return (elem.strftime('%Y:%m') for elem in stop_excluding_rrset(MONTHLY, start, stop))
        
    def get_yearly_suffixes(self, now, start=None, stop=None):
        if not start:
            start = parse(now.strftime(DT_PATTERNS.CURRENT_YEAR_START))
        if not stop:
            delta = relativedelta(now, years=1)
            stop = parse((start - delta).strftime(DT_PATTERNS.PREVIOUS_YEAR_END))
        
        return (elem.strftime('%Y') for elem in stop_excluding_rrset(YEARLY, start, stop))
    
    def _get_patterns(self, now, start, stop, kvdb_key, method):
        return ('{}*:{}'.format(kvdb_key, elem) for elem in method(now, start, stop))
    
    def get_by_minute_patterns(self, now, start=None, stop=None):
        return self._get_patterns(now, start, stop, KVDB.SERVICE_TIME_AGGREGATED_BY_MINUTE, self.get_minutely_suffixes)
    
    def get_by_hour_patterns(self, now, start=None, stop=None):
        return self._get_patterns(now, start, stop, KVDB.SERVICE_TIME_AGGREGATED_BY_HOUR, self.get_hourly_suffixes)
    
    def get_by_day_patterns(self, now, start=None, stop=None):
        return self._get_patterns(now, start, stop, KVDB.SERVICE_TIME_AGGREGATED_BY_DAY, self.get_daily_suffixes)
    
    def get_by_month_patterns(self, now, start=None, stop=None):
        return self._get_patterns(now, start, stop, KVDB.SERVICE_TIME_AGGREGATED_BY_MONTH, self.get_monthly_suffixes)
    
    def create_summary(self, target, *pattern_names):
        now = datetime.utcnow()
        key_prefix = KVDB.SERVICE_SUMMARY_PREFIX_PATTERN.format(target)
        
        if target == 'by-week':
            start = parse((now + relativedelta(weekday=MO(-1))).strftime('%Y-%m-%d 00:00:00')) # Current week start
            key_suffix = start.strftime(DT_PATTERNS.SUMMARY_SUFFIX_PATTERNS[target])
        else:
            start = parse(now.strftime('%Y-%m-%d 00:00:00')) # Current day start
            key_suffix = now.strftime(DT_PATTERNS.SUMMARY_SUFFIX_PATTERNS[target])
        total_seconds = (now - start).total_seconds()
        
        patterns = []
        for name in pattern_names:
            patterns.append(getattr(self, 'get_by_{}_patterns'.format(name))(now))
        
        services = {}
        
        for elem in chain(*patterns):
            prefix, suffix = elem.split('*')
            suffix = suffix[1:]
            stats = self.collect_service_stats(elem, prefix, suffix, None, False, False, False)
            
            for service_name, values in stats.items():
                stats = services.setdefault(service_name, deepcopy(DEFAULT_STATS))
                
                for name in STATS_KEYS:
                    value = values[name]
                    if name == 'usage':
                        stats[name] += value
                    elif name == 'max':
                        stats[name] = max(stats[name], value)
                    elif name == 'mean':
                        stats[name].append(value)
                    elif name == 'min':
                        stats[name] = min(stats[name], value)
                        
        for service_name, values in services.items():
            values['mean'] = round(sp_stats.tmean(values['mean']), 2)
            values['rate'] = round(values['usage'] / total_seconds, 2)
            
        self.hset_aggr_keys(services, key_prefix, key_suffix)
        
# ##############################################################################

class CreateSummaryByDay(BaseSummarizingService):
    """ Creates a summary for the current day.
    """
    def handle(self):
        self.create_summary('by-day', 'hour', 'minute')

class CreateSummaryByWeek(BaseSummarizingService):
    def handle(self):
        self.create_summary('by-week', 'day', 'hour', 'minute')

class CreateSummaryByMonth(BaseSummarizingService):
    def handle(self):
        self.create_summary('by-month', 'day', 'hour', 'minute')

class CreateSummaryByYear(BaseSummarizingService):
    def handle(self):
        self.create_summary('by-year', 'month', 'day', 'hour', 'minute')
        
# ##############################################################################

class GetSummaryBase(StatsReturningService):
    """ A base class for returning the summary of statistics for a given period.
    """
    class SimpleIO(StatsReturningService.SimpleIO):
        input_required = ('start', 'n', 'n_type')
        
    stats_key_prefix = None
    
    def get_start_date(self):
        return self.request.input.start
    
    def get_end_date(self, start):
        raise NotImplementedError('Should be implemented by subclasses')

    def get_suffixes(self, start, _ignored):
        # Note that the caller expects a list of patterns while a summary is for
        # a concrete date. Hence we're returning a one-element list to make everyone happy.
        return [start.strftime(DT_PATTERNS.SUMMARY_SUFFIX_PATTERNS[self.summary_type])]
    
    def handle(self):
        self.response.payload[:] = (elem.to_dict() for elem in self.get_stats(
            self.get_start_date(), self.get_end_date(self.request.input.start), 
            n=self.request.input.n, n_type=self.request.input.n_type, needs_trends=False))

class GetSummaryByDay(GetSummaryBase):
    summary_type = 'by-day'
    stats_key_prefix = KVDB.SERVICE_SUMMARY_BY_DAY
    
    def get_end_date(self, start):
        if start == date.today().isoformat():
            return datetime.utcnow().isoformat()
        else:
            return '{}T23:59:59'.format(start)

class GetSummaryByWeek(GetSummaryBase):
    summary_type = 'by-week'
    stats_key_prefix = KVDB.SERVICE_SUMMARY_BY_WEEK
    
    def get_start_date(self):
        """ Find the nearest Monday preceding the start date given on input.
        """
        return (parse(self.request.input.start) + relativedelta(weekday=MO(-1))).strftime('%Y-%m-%d')
    
    def get_end_date(self, start):
        start = parse(start)
        today = date.today()
        
        # Is it the current week?
        if start.year == today.year and start.isocalendar()[1] == today.isocalendar()[1]:
            return datetime.utcnow().isoformat()
            
        # It's not the current one, find Sunday nearest to the start
        else:
            return (start + relativedelta(weekday=SU(+1))).strftime('%Y-%m-%d 23:59:59')
    
class GetSummaryByMonth(GetSummaryBase):
    summary_type = 'by-month'
    stats_key_prefix = KVDB.SERVICE_SUMMARY_BY_MONTH
    
    def get_end_date(self, start):
        if start == date.today().strftime('%Y-%m'):
            return datetime.utcnow().isoformat()
        else:
            start = parse(start)
            return '{0}-{1:0>2}-{2}T23:59:59'.format(start.year, start.month, monthrange(start.year, start.month)[1])

class GetSummaryByYear(GetSummaryBase):
    summary_type = 'by-year'
    stats_key_prefix = KVDB.SERVICE_SUMMARY_BY_YEAR

    def get_end_date(self, start):
        if date.today().isoformat().startswith(start):
            return datetime.utcnow().isoformat()
        else:
            return '{}-12-31T23:59:59'.format(start)

class GetSummaryByRange(StatsReturningService, BaseSummarizingService):
    """ Returns a summary of statistics across a range of UTC start and stop parameters.
    """
    MINIMUM_DIFFERENCE = 3 # In minutes
    SLICE_TYPE_METHOD = {
        KVDB.SERVICE_TIME_AGGREGATED_BY_MINUTE: 'minutely',
        KVDB.SERVICE_TIME_AGGREGATED_BY_HOUR: 'hourly',
        KVDB.SERVICE_SUMMARY_BY_DAY: 'daily',
        KVDB.SERVICE_SUMMARY_BY_MONTH: 'monthly',
        KVDB.SERVICE_SUMMARY_BY_YEAR: 'yearly',
    }
    
    def by_minutes(self, start, stop):
        return SummarySlice(KVDB.SERVICE_TIME_AGGREGATED_BY_MINUTE, start, stop)
        
    def by_hours(self, start, stop):
        return SummarySlice(KVDB.SERVICE_TIME_AGGREGATED_BY_HOUR, start, stop)
        
    def by_days(self, start, stop):
        return SummarySlice(KVDB.SERVICE_SUMMARY_BY_DAY, start, stop)
        
    def by_months(self, start, stop):
        return SummarySlice(KVDB.SERVICE_SUMMARY_BY_MONTH, start, stop)
        
    def by_years(self, start, stop):
        return SummarySlice(KVDB.SERVICE_SUMMARY_BY_YEAR, start, stop)
        
    def _slice_with_inserted_hours(self, start, stop):
        """ start=2012-10-23T20:15:00, stop=2012-10-23T22:11:00
        or start=2012-10-22T17:19:00, stop=2012-10-23T11:49:00
        
        We can extract three pieces in this case
        by-minutes 2012-10-23T20:15:00 - 2012-10-23T21:00:00
        by-hours   2012-10-23T21:00:00 - 2012-10-23T22:00:00
        by-minutes 2012-10-23T21:00:00 - 2012-10-23T22:11:00
        """
        slice_in_between_start = datetime(year=start.year, 
            month=start.month, day=start.day, hour=start.hour) + relativedelta(hours=1)
            
        slice_in_between_stop = datetime(year=stop.year, 
            month=stop.month, day=stop.day, hour=stop.hour)
    
        yield self.by_minutes(start, slice_in_between_start)
        yield self.by_hours(slice_in_between_start, slice_in_between_stop)
        yield self.by_minutes(slice_in_between_stop, stop)
        
    def _slice_full_start_hour(self, start, stop):
        """ start=2012-10-22T17:00:00 stop=2012-10-23T14:37:00
        (or start=2012-10-23T20:00:00, stop=2012-10-23T21:19:00)
        
        We slice it into two pieces
        by-hours   2012-10-11T20:00:00 - 2012-10-11T21:00:00
        by-minutes 2012-10-11T21:00:00 - 2012-10-11T21:19:00
        """
        stop_start_hour = datetime(year=stop.year, month=stop.month, day=stop.day, hour=stop.hour)
        yield self.by_hours(start, stop_start_hour)
        yield self.by_minutes(stop_start_hour, stop)
        
    def _get_slices_by_hours(self, start, stop, delta):
        """ Used when the difference between start and stop is less than one day.
        """
        if start.minute == 0:
            for slice in self._slice_full_start_hour(start, stop):
                yield slice
        else:
            if delta.hours == 1:
                if delta.minutes == 0:
                    # start=2012-10-23T20:00:00, stop=2012-10-23T21:00:00
                    yield self.by_hours(start, stop)
                else:
                    if stop.hour - start.hour == 2:
                        # start=2012-10-23T20:15:00, stop=2012-10-23T22:11:00
                        for slice in self._slice_with_inserted_hours(start, stop):
                            yield slice
                    else:
                        # start=2012-10-23T20:15:00, stop=2012-10-23T21:37:00
                        # There's nothing we can do except slicing it up by minutes
                        yield self.by_minutes(start, stop)
            else:
                # start=2012-10-22T17:19:00, stop=2012-10-23T11:49:00
                for slice in self._slice_with_inserted_hours(start, stop):
                    yield slice
                    
    def _get_slices_by_days(self, start, stop, delta):
        """ start=2012-12-02T19:17:00, stop=2013-01-19T17:19:00
        """
        slice_in_between_start = datetime(year=start.year, month=start.month, day=start.day) + relativedelta(days=1)
        slice_in_between_stop = datetime(year=stop.year, month=stop.month, day=stop.day)
    
        if start.hour or start.minute:
            for slice in self._get_slices_by_hours(start, slice_in_between_start, relativedelta(
                                                       start, slice_in_between_start)):
                yield slice
                
            yield self.by_days(slice_in_between_start, slice_in_between_stop)
            
        else:
            # It's a midnight
            yield self.by_days(start, slice_in_between_stop)
    
        for slice in self._get_slices_by_hours(slice_in_between_stop, stop, relativedelta(slice_in_between_stop, stop)):
            yield slice
            
    def _get_slices_by_months(self, start, stop, delta):
        """ start=2012-11-02T19:17:00, stop=2013-01-19T17:19:00
        """
        slice_in_between_start = datetime(year=start.year, month=start.month, day=1) + relativedelta(months=1)
        slice_in_between_stop = datetime(year=stop.year, month=stop.month, day=1)
    
        if start.day != 1:
            for slice in self._get_slices_by_days(start, slice_in_between_start, relativedelta(
                                                      start, slice_in_between_start)):
                yield slice
                
            yield self.by_months(slice_in_between_start, slice_in_between_stop)
            
        else:
            # First day of a month
            yield self.by_months(start, slice_in_between_stop)
    
        for slice in self._get_slices_by_days(slice_in_between_stop, stop, relativedelta(slice_in_between_stop, stop)):
            yield slice
            
    def _get_slices_by_years(self, start, stop, delta):
        """ start=2012-05-07T19:43:00, stop=2014-11-23T21:19:00
        """
        slice_in_between_start = datetime(year=start.year, month=1, day=1) + relativedelta(years=1)
        slice_in_between_stop = datetime(year=stop.year, month=1, day=1)
    
        for slice in self._get_slices_by_months(start, slice_in_between_start, relativedelta(start, slice_in_between_start)):
            yield slice
            
        yield self.by_years(slice_in_between_start, slice_in_between_stop)
    
        for slice in self._get_slices_by_months(slice_in_between_stop, stop, relativedelta(slice_in_between_stop, stop)):
            yield slice
    
    def get_slices(self, orig_start, orig_stop):
        """ Slices the time range into a series of per-minute/-hour/-day/-month or -year statistics.
        """
        slices = []
        start = parse(orig_start)
        stop = parse(orig_stop)
    
        delta = relativedelta(stop, start)
        print(dir(delta))
        
        by_mins = not any((delta.years, delta.months, delta.days, delta.hours))
        by_hours_mins = not any((delta.years, delta.months, delta.days))
        by_days_hours_mins = not any((delta.years, delta.months))
        by_months_days_hours_mins = not delta.years
        
        print(delta, delta.years, delta.months, delta.days, delta.hours, by_mins, by_hours_mins, by_days_hours_mins, by_months_days_hours_mins)
        
        # Sanity check, find out whether more than one predicate is True.
        predicates = (by_mins, by_hours_mins, by_days_hours_mins, by_months_days_hours_mins)
        sum_preds = sum(int(elem) for elem in predicates)
        if sum_preds > 1:
            msg = 'sum:[{}] of predicates:[{}] is > 1, delta:[{}, {} {} {} {}], start:[{}], stop:[{}]'.format(
                sum_preds, predicates, delta, delta.years, delta.months, delta.days, delta.hours, start, stop)
            raise ZatoException(self.cid, msg)
        
        # We require that start and stop be at least that many minutes apart and, obviosuly,
        # that start lives farther in the past.
        if by_mins and delta.minutes < self.MINIMUM_DIFFERENCE:
            raise ValueError(
                'stop and start must be at least [{}] minutes apart, start must be '\
                'farther in past; start:[{}], stop:[{}]'.format(
                   self.MINIMUM_DIFFERENCE, orig_start, orig_stop))
                   
        if by_mins:
            # start=2012-10-23T20:13:00, stop=2012-10-23T21:07:00
            slices.append(self.by_minutes(start, stop))
            
        elif by_hours_mins:
            for slice in self._get_slices_by_hours(start, stop, delta):
                slices.append(slice)
                        
        elif by_days_hours_mins or (by_months_days_hours_mins and delta.months == 1):
            for slice in self._get_slices_by_days(start, stop, delta):
                slices.append(slice)
                
        elif by_months_days_hours_mins:
            for slice in self._get_slices_by_months(start, stop, delta):
                slices.append(slice)
                
        else:
            for slice in self._get_slices_by_years(start, stop, delta):
                slices.append(slice)
            
        return slices
    
    def merge_slices(self, slices):
        """ Merges a list of stats slices into a single aggregated elem.
        """
        stats_elems = {}
        
        total_seconds = 0
        all_services_usage = 0
        all_services_time = 0
        
        # This is a sum of all per-service usage elems across all slices. 
        # Later on it will be divided by the total number of seconds.
        service_usage = {}
        
        for slice in slices:
            for stats in slice.stats:
                
                print()
                print(slice.start, slice.stop, stats.to_dict())
                
                '''
                stats_elem = stats_elems.setdefault(stats.service_name, StatsElem(stats.service_name))
                stats_elem += stats

                total_seconds += slice.total_seconds
                stats_elem.all_services_usage = stats.all_services_usage
                stats_elem.all_services_time = stats.all_services_time
                '''
                
                #if stats.service_name == 'zato.stats.GetByService':
                #    print(stats.to_dict())
                #print()

            #print()
            #print()
            #print()

        #for service_name, stats_elem in stats_elems.items():
        #    stats_elem.all_services_usage = all_services_usage
        #    stats_elem.all_services_time = all_services_time
            
        '''
        if total_seconds:
            for service_name, stats_elem in stats_elems.items():
                stats_elem.rate = round(stats_elem.usage / total_seconds, 5)
                    
        from pprint import pprint
        
        for k, v in sorted(stats_elems.items()):
            print(k)
            pprint(v.to_dict())
            print()
        
        #pprint(stats_elems['zato.stats.GetByService'].to_dict())
        #print()
        '''
            
    
    def handle(self):
        
        start = '2012-10-14T23:56:49'
        stop = '2012-10-15T00:56:49'
        
        if(self.logger.isEnabledFor(logging.DEBUG)):
            self.logger.DEBUG(
                'Getting slices for start:[{}], stop:[{}]'.format(self.request.input.start, self.request.input.stop))
        
        slices = []
        
        for slice in self.get_slices(start, stop):
            print(slice)
            '''
            if slice.total_seconds:
                get_suffixes_method = getattr(self, 'get_{}_suffixes'.format(self.SLICE_TYPE_METHOD[slice.slice_type]))
                suffixes = tuple(get_suffixes_method(None, slice.start, slice.stop))
                
                start_iso = slice.start.isoformat()
                stop_iso = slice.stop.isoformat()
                
                stats = self.get_stats(start_iso, stop_iso, needs_trends=False, 
                    stats_key_prefix=slice.slice_type, suffixes=suffixes)
                slices.append(SliceStats(slice.slice_type, stats, start_iso, stop_iso, slice.total_seconds))
                '''
                
        #stats = self.merge_slices(slices)
                    
        '''for slice_stats in all_slice_stats:
            for stats in slice_stats.stats:
                print(stats.to_dict())
            print()
            '''

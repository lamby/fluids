# -*- coding: utf-8 -*-
'''Chemical Engineering Design Library (ChEDL). Utilities for process modeling.
Copyright (C) 2016, 2017 Caleb Bell <Caleb.Andrew.Bell@gmail.com>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.'''

from __future__ import division
try: # pragma: no cover
    from cStringIO import StringIO
except: # pragma: no cover
    from io import BytesIO as StringIO
import os
import gzip
import datetime
from collections import namedtuple

import numpy as np
from fluids.core import F2K
from scipy.constants import mile, knot, inch
from scipy.spatial import KDTree, cKDTree
from scipy.stats import scoreatpercentile


try: # pragma: no cover
    from urllib.request import urlopen
    from urllib.error import HTTPError
except ImportError: # pragma: no cover
    from urllib2 import urlopen
    from urllib2 import HTTPError
    
try:  # pragma: no cover
    from appdirs import user_data_dir, user_config_dir
    data_dir = user_config_dir('fluids')
except ImportError:  # pragma: no cover
    data_dir = ''
    pass
    
__all__ = ['get_clean_isd_history', 'IntegratedSurfaceDatabaseStation',
           'get_closest_station', 'get_station_year_text', 'gsod_day_parser',
           'StationDataGSOD']

folder = os.path.join(os.path.dirname(__file__), 'data')


def get_clean_isd_history(dest=os.path.join(folder, 'isd-history-cleaned.tsv'),
                          url="ftp://ftp.ncdc.noaa.gov/pub/data/noaa/isd-history.csv"): # pragma: no cover
    '''Basic method to update the isd-history file from the NOAA. This is 
    useful as new weather stations are updated all the time.
    
    This function requires pandas to run. If fluids is installed for the 
    superuser, this method must be called in an instance of Python running
    as the superuser (administrator).
    
    Retrieving the file from ftp typically takes several seconds.
    Pandas reads the file in ~30 ms and writes it in ~220 ms. Reading it with 
    the code below takes ~220 ms but is necessary to prevent a pandas 
    dependency.
    
    Parameters
    ----------
    dest : str, optional
        The file to store the data retrieved; leave as the default argument
        for it to be accessible by fluids.
    url : str, optional
        The location of the data file; this can be anywhere that can be read
        by pandas, including a local file as would be useful in an offline
        situation.
    '''
    import pandas as pd
    df = pd.read_csv(url)
    df.to_csv(dest, sep='\t', index=False, header=False)

def days_in_year(year):
    if year %4 == 0 and (not year % 100 == 0 or year % 400 == 0):
        return 366
    return 365


class IntegratedSurfaceDatabaseStation(object):
    '''Class to hold data on a weather station in the Integrated Surface
    Database.
    
    License information for the database can be found at the following link:
    https://data.noaa.gov/dataset/global-surface-summary-of-the-day-gsod

    Parameters
    ----------
    USAF : int or None if unassigned
        Air Force station ID. May contain a letter in the first position.
    WBAN : int or None if unassigned
        NCDC WBAN number
    NAME : str
        Name of the station; ex. 'CENTRAL COLORADO REGIONAL AP'
    CTRY : str or None if unspecified
        FIPS country ID
    ST : str or None if not in the US
        State for US stations
    ICAO : str or None if not an airport
        ICAO airport code
    LAT : float
        Latitude with a precision of one thousandths of a decimal degree, 
        [degrees]
    LON : float
        Longitude with a precision of one thousandths of a decimal degree, 
        [degrees]
    ELEV : float
        Elevation of weather station, [m]
    BEGIN : float
        Beginning Period Of Record (YYYYMMDD). There may be reporting gaps 
        within the P.O.R.
    END : Ending Period Of Record (YYYYMMDD). There may be reporting gaps
        within the P.O.R.
    '''
    __slots__ = ['USAF', 'WBAN', 'NAME', 'CTRY', 'ST', 'ICAO', 'LAT', 'LON',
                 'ELEV', 'BEGIN', 'END', 'raw_data', 'parsed_data']
    
    def __repr__(self):
        s = ('<Weather station registered in the Integrated Surface Database, '
            'name %s, country %s, USAF %s, WBAN %s, coords (%s, %s) '
            'Weather data from %s to %s>' )
        return s%(self.NAME, self.CTRY, self.USAF, self.WBAN, self.LAT, self.LON, str(self.BEGIN)[0:4], str(self.END)[0:4])
    
    def __init__(self, USAF, WBAN, NAME, CTRY, ST, ICAO, LAT, LON, ELEV, BEGIN,
                 END):
        try:
            self.USAF = int(USAF)
        except TypeError:
            self.USAF = USAF # Nones
        try:
            self.WBAN = int(WBAN)  
        except TypeError:
            self.WBAN = WBAN
        self.NAME = NAME
        self.CTRY = CTRY
        self.ST = ST
        self.ICAO = ICAO
        self.LAT = LAT
        self.LON = LON
        self.ELEV = ELEV
        self.BEGIN = int(BEGIN)
        self.END = int(END)
        

class StationDataGSOD(object):
    # Holds data, caches and retrieves data
    def __init__(self, station):
        self.station = station
        self.begin = datetime.datetime.strptime(str(self.station.BEGIN), '%Y%m%d')
        self.end = datetime.datetime.strptime(str(self.station.END), '%Y%m%d')
        
        self.year_range = range(self.begin.year, self.end.year + 1)
        
#         Would be nice to create these later, when using a download_data method
        self.raw_text = {}
        self.raw_data = {}
        self.parsed_data = {}
        self.load_empty_vectors()
        
    def load_empty_vectors(self):
        for year in self.year_range:
            self.raw_data[year] = [None]*days_in_year(year)
            self.parsed_data[year] = [None]*days_in_year(year)
            self.raw_text[year] = None
#        days = [None]*days_in_year(y)

    def download_data(self):
        for year in self.year_range:
            if self.raw_text[year] is None:
                try:
                    year_data = get_station_year_text(self.station.USAF, self.station.WBAN, year)
                    self.raw_text[year] = year_data
                except:
                    pass
            
            
stations = []
_latlongs = []
'''Read in the parsed data into 
1) a list of latitudes and longitudes, temporary, which will get converted to
a numpy array for use in KDTree
2) a list of IntegratedSurfaceDatabaseStation objects; the query will return
the index of the nearest weather stations.
'''
with open(os.path.join(folder, 'isd-history-cleaned.tsv')) as f:
    for line in f:
        values = line.split('\t')
        for i in range(0, 11):
            v = values[i]
            if not v:
                values[i] = None # '' case
            else:
                try:
                    values[i] = float(v)
                    if int(v) == 99999:
                        values[i] = None
                except:
                    continue
        lat, lon = values[6], values[7]
        if lat and lon:
            # Some stations have no lat-long; this isn't useful
            stations.append(IntegratedSurfaceDatabaseStation(*values))
            _latlongs.append((lat, lon))
_latlongs = np.array(_latlongs)
station_count = len(stations)


kd_tree = cKDTree(_latlongs) # _latlongs must be unchanged as data is not copied


def get_closest_station(latitude, longitude, minumum_recent_data=20140000, 
                        match_max=100):
    '''Query function to find the nearest weather station to a particular 
    set of coordinates. Optionally allows for a recent date by which the 
    station is required to be still active at.
    
    Parameters
    ----------
    latitude : float
        Latitude to search for nearby weather stations at, [degrees]
    longitude : float
        Longitude to search for nearby weather stations at, [degrees]
    minumum_recent_data : int, optional
        Date that the weather station is required to have more recent
        weather data than; format YYYYMMDD; set this to 0 to not restrict data
        by date.
    match_max : int, optional
        The maximum number of results in the KDTree to search for before 
        applying the filtering criteria; an internal parameter which is
        increased automatically if the default value is insufficient [-]
        
    Returns
    -------
    station : IntegratedSurfaceDatabaseStation
        Instance of IntegratedSurfaceDatabaseStation which was nearest
        to the requested coordinates and with sufficiently recent data
        available [-]
        
    Notes
    -----
    Searching for 100 stations is a reasonable choice as it takes, ~70 
    microseconds vs 50 microsecond to find only 1 station. The search does get 
    slower as more points are requested. Bad data is returned from a KDTree
    search if more points are requested than are available.
    
    Examples
    --------
    >>> get_closest_station(51.02532675, -114.049868485806, 20150000)
    <Weather station registered in the Integrated Surface Database, name CALGARY INTL CS, country CA, USAF 713930.0, WBAN None, coords (51.1, -114.0) Weather data from 2004 to 2017>
    '''
    # Both station strings may be important
    # Searching for 100 stations is fine, 70 microseconds vs 50 microsecond for 1
    # but there's little point for more points, it gets slower.
    # bad data is returned if k > station_count
    distances, indexes = kd_tree.query([latitude, longitude], k=min(match_max, station_count)) 
    #
    for i in indexes:
        latlon = _latlongs[i]
        enddate = stations[i].END
        # Iterate for all indexes until one is found whose date is current
        if enddate > minumum_recent_data:
            return stations[i]
    if match_max < station_count:
        return get_closest_station(latitude, longitude, minumum_recent_data=minumum_recent_data, match_max=match_max*10)
    raise Exception('Could not find a station with more recent data than '
                    'specified near the specified coordinates.')


# This should be agressively cached
def get_station_year_text(WMO, WBAN, year):
    '''Basic method to download data from the GSOD database, given a 
    station idenfifier and year. 

    Parameters
    ----------
    WMO : int or None
         World Meteorological Organization (WMO) identifiers, [-]
    WBAN : int or None
        Weather Bureau Army Navy (WBAN) weather station identifier, [-]
    year : int
        Year data should be retrieved from, [year]
        
    Returns
    -------
    data : str
        Downloaded data file
    '''
    if WMO is None:
        WMO = 999999
    if WBAN is None:
        WBAN = 99999
    station = str(int(WMO)) + '-' + str(int(WBAN)) 
    gsod_year_dir = os.path.join(data_dir, 'gsod', str(year))
    path = os.path.join(gsod_year_dir, station + '.op')
    if os.path.exists(path):
        data = open(path).read()
        if data:
            return data
        
    toget = ('ftp://ftp.ncdc.noaa.gov/pub/data/gsod/' + str(year) + '/' 
             + station + '-' + str(year) +'.op.gz')
    try:
        data = urlopen(toget, timeout=5)
    except Exception as e:
        raise Exception('Could not obtain desired data; check '
                        'if the year has data published for the '
                        'specified station and the station was specified '
                        'in the correct form. The full error is %s' %(e))
        
    data = data.read()
    data_thing = StringIO(data)

    f = gzip.GzipFile(fileobj=data_thing, mode="r")
    year_station_data = f.read()
    try: 
        year_station_data = year_station_data.decode('utf-8')
    except:
        pass
    
    # Cache the data for future use
    if not os.path.exists(gsod_year_dir):
        os.makedirs(gsod_year_dir)
    open(path, 'w').write(year_station_data)
    
    
    return year_station_data
    


gsod_fields = ['DATE', # 15-18 int year; 19-22 int month/day
               'TEMP', # 25-30 Real Mean temperature for the day in degrees Fahrenheit to tenths. Missing = 9999.9
               'TEMP_COUNT', # 32-33 Int. Number of observations used in calculating mean temperature
               'DEWP', # 36-41 Real Mean dew point for the day in degrees Fahrenheit to tenths.  Missing = 9999.9
               'DEWP_COUNT', # 43-44 Int. Number of observations used in calculating mean dew point
               'SLP', # 47-52 Real Mean sea level pressure for the day in millibars to tenths.  Missing = 9999.9
               'SLP_COUNT', # 54-55 Int. Number of observations used in calculating mean sea level pressure
               'STP', # 58-63 Real Mean station pressure for the day in millibars to tenths. Missing = 9999.9
               'STP_COUNT', # 65-66 Int. Number of observations used in calculating mean station pressure
               'VISIB', # 69-73 Real Mean visibility for the day in miles to tenths. Missing = 999.9
               'VISIB_COUNT', # 75-76 Int. Number of observations used in calculating mean visibility
               'WDSP', # 79-83 Real Mean wind speed for the day in knots to tenths. Missing = 999.9
               'WDSP_COUNT', # 85-86 Int. Number of observations used in calculating mean wind speed
               'MXSPD', # 89-93 Real Maximum sustained wind speed reported for the day in knots to tenths. Missing = 999.9
               'GUST', # 96-100 Real Maximum wind gust reported for the day in knots to tenths. Missing = 999.9
               'MAX', # 103-108 Real Maximum temperature reported during the 
                      # day in Fahrenheit to tenths--time of max temp report varies by country and
                      # region, so this will sometimes not be the max for the calendar day.
                      # Missing = 9999.9; FLAG of '*' is present on 109-109!
               'MIN', # 111-116 Real Minimum temperature reported during the day in Fahrenheit to tenths--time of min
                      # temp report varies by country and region, so this will sometimes not be 
                      # the min for the calendar day. Missing = 9999.9 FLAG of '*' is present on 117-117!
               'PRCP', # 119-123 Real Total precipitation (rain and/or melted snow) reported during the day in inches
                       # and hundredths; will usually not end with the midnight observation--i.e.,
                       # may include latter part of previous day. .00 indicates no measurable
                       # precipitation (includes a trace).
                       # Missing = 99.99

               'SNDP', # 126-130 Real Snow depth in inches to tenths--last report for the day if reported more than
                       # once.  Missing = 999.9 Note: Most stations do not report '0' on days with no snow on the
                       # ground--therefore, '999.9' will often appear on these days.
               'FRSHTT' # 133-138 Int. Indicators (1 = yes, 0 = no/not reported) for the occurrence during the day of:
                        # Fog ('F' - 1st digit).
                        # Rain or Drizzle ('R' - 2nd digit).
                        # Snow or Ice Pellets ('S' - 3rd digit).
                        # Hail ('H' - 4th digit).
                        # Thunder ('T' - 5th digit).
                        # Tornado or Funnel Cloud ('T' - 6th digit).              
              ]

# Values to be converted to floats always
gsod_float_fields = ('TEMP', 'DEWP', 'SLP', 'STP', 'VISIB', 'WDSP', 'MXSPD', 
                     'GUST', 'MAX', 'MIN', 'PRCP', 'SNDP')
# Values to be converted to ints always
gsod_int_fields = ('TEMP_COUNT', 'DEWP_COUNT', 'SLP_COUNT', 'STP_COUNT', 
                   'VISIB_COUNT', 'WDSP_COUNT')

# Values which signify flags
gsod_flag_chars = '*ABCDEFGHI'
# Values which should be converted to None, as normally there is no value
gsod_bad_values = set(['99.99', '999.9', '9999.9'])

gsod_indicator_names = ['fog', 'rain', 'snow_ice', 'hail', 'thunder', 
                        'tornado']
five_ninths = 5.0/9.0

gsod_day = namedtuple('gsod_day', gsod_fields + gsod_indicator_names)


def gsod_day_parser(line, SI=True, datetime=True):
    '''One line (one file) parser of data in the format of the GSOD database.
    Returns all parsed results as a namedtuple for reduced memory consumption.
    Will convert all data to base SI units unless the `SI` flag is set to 
    False. As the values are rounded to one or two decimal places in the
    GSOD database in Imperial units, it may be useful to look at the values
    directly. 
    
    The names columns of the columns in the GSOD database are retained and used
    as the attributes of the namedtuple results.
    
    The day, month, and year are normally converted to a datetime instance in
    resulting namedtuple; this behavior can be disabled by setting the 
    `datetime` flag to False; it will be a string in the format YYYYMMDD if so.
    This may be useful because datetime conversion roughly doubles the speed of
    this function.
    
    Parameters
    ----------
    line : str
        Line in format of GSOD documentation, [-]
    SI : bool
        Whether or not the results get converted to base SI units, [-] 
    datetime : bool
        Whether or not the date gets converted to a datetime instance or stays
        as a string, [-]

    Returns
    -------
    gsod_day_instance : gsod_day
        namedtuple with fields described in the source (all values in SI units,
        if `SI` is True, i.e. meters, m/s, Kelvin, Pascal; otherwise the 
        original unit set is used), [-]
    '''    
    # Ignore STN--- and WBAN, 8-12 characters
    fields = line.strip().split()[2:]
    # For the case the field is blank, set it to None; strip it either way 
    for i in range(len(fields)):
        field = fields[i].strip()
        if not field:
            field = None
        fields[i] = field 

    obj = dict(zip(gsod_fields, fields))
    # Convert the date to a datetime object if specified
    if datetime:
        if obj['DATE']:
            obj['DATE'] = datetime.datetime.strptime(obj['DATE'], '%Y%m%d')
                
    # Parse float values as floats
    for field in gsod_float_fields:
        value = obj[field].rstrip(gsod_flag_chars)
        if value in gsod_bad_values:
            value = None
        else:
            value = float(value)
        obj[field] = value
        
    if SI:
        # All temperatures are in deg F
        for field in ('TEMP', 'DEWP', 'MAX', 'MIN'):
            value = obj[field]
            if value is not None:
                # F2K inline for efficiency unfortunately
                obj[field] = (value + 459.67)*five_ninths

        # Convert visibility, wind speed, pressures
        # to si units of meters, Pascal, and meters/second.
        if obj['VISIB'] is not None:
            obj['VISIB'] = obj['VISIB']*mile
        if obj['PRCP'] is not None:
            obj['PRCP'] = obj['PRCP']*inch
        if obj['SNDP'] is not None:
            obj['SNDP'] = obj['SNDP']*inch
        if obj['WDSP'] is not None:
            obj['WDSP'] = obj['WDSP']*knot 
        if obj['MXSPD'] is not None:
            obj['MXSPD'] = obj['MXSPD']*knot
        if obj['GUST'] is not None:
            obj['GUST'] = obj['GUST']*knot
        if obj['SLP'] is not None:
            obj['SLP'] = obj['SLP']*100.0
        if obj['STP'] is not None:
            obj['STP'] = obj['STP']*100.0

    # Parse int values as ints
    for field in gsod_int_fields:
        value = obj[field] 
        if value is not None:
            obj[field] = int(value)

    indicator_values = [flag == '1' for flag in obj['FRSHTT']]
    obj.update(zip(gsod_indicator_names, indicator_values))
    return gsod_day(**obj)

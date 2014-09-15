# (C) British Crown Copyright 2014, Met Office
#
# This file is part of Iris.
#
# Iris is free software: you can redistribute it and/or modify it under
# the terms of the GNU Lesser General Public License as published by the
# Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Iris is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with Iris.  If not, see <http://www.gnu.org/licenses/>.
"""
Defines a lightweight wrapper class to wrap a single GRIB message.

"""

from collections import OrderedDict
import re

import gribapi

from iris.exceptions import TranslationError


class _GribMessage(object):
    """
    Lightweight GRIB message wrapper, containing **only** the coded keys and
    data attribute of the input GRIB message.

    """

    def __init__(self, raw_message):
        """

        Args:

        * raw_message:
            The _RawGribMessage instance which should be wrapped to
            provide the `data` attribute.

        """
        self._raw_message = raw_message

    @property
    def sections(self):
        return self._raw_message.sections

    @property
    def data(self):
        """
        The data array from the GRIB message.

        The shape of the array will match the logical shape of the
        message's grid. For example, a simple global grid would be
        available as a 2-dimensional array with shape (Nj, Ni).

        """
        sections = self.sections
        grid_section = sections[3]
        if grid_section['sourceOfGridDefinition'] != 0:
            raise TranslationError(
                'Unsupported source of grid definition: {}'.format(
                    grid_section['sourceOfGridDefinition']))

        if (grid_section['numberOfOctectsForNumberOfPoints'] != 0 or
                grid_section['interpretationOfNumberOfPoints'] != 0):
            raise TranslationError('Grid Definition Section 3 contains '
                                   'unsupported quasi-regular grid.')

        template = grid_section['gridDefinitionTemplateNumber']
        if template == 0:
            data = sections[7]['codedValues'].reshape(grid_section['Nj'],
                                                      grid_section['Ni'])
        else:
            fmt = 'Grid definition template {} is not supported'
            raise TranslationError(fmt.format(template))
        return data


class _RawGribMessage(object):
    """
    Lightweight GRIB message wrapper, containing **only** the coded keys
    of the input GRIB message.

    """
    _NEW_SECTION_KEY_MATCHER = re.compile(r'section([0-9]{1})Length')

    def __init__(self, message_id):
        """
        A _RawGribMessage object contains the **coded** keys from a
        GRIB message that is identified by the input message id.

        Args:

        * message_id:
            An integer generated by gribapi referencing a GRIB message within
            an open GRIB file.

        """
        self._message_id = message_id
        self._sections = None

    def __del__(self):
        """
        Release the gribapi reference to the message at end of object's life.

        """
        gribapi.grib_release(self._message_id)

    @property
    def sections(self):
        """
        Return the key-value pairs of the message keys, grouped by containing
        section.

        Key-value pairs are collected into a dictionary of
        :class:`collections.OrderedDict` objects. One such object is made for
        each section in the message, such that the section number is the
        object's key in the containing dictionary. Each object contains
        key-value pairs for all of the message keys in the given section.

        .. warning::
            This currently does **not** return only the coded keys from a
            message. This is because the gribapi functionality needed to
            achieve this is broken, with a fix available from gribapi v1.13.0.

        """
        if self._sections is None:
            self._sections = self._get_message_sections()
        return self._sections

    def _get_message_keys(self):
        """Creates a generator of all the keys in the message."""

        keys_itr = gribapi.grib_keys_iterator_new(self._message_id)
        gribapi.grib_skip_computed(keys_itr)
        while gribapi.grib_keys_iterator_next(keys_itr):
            yield gribapi.grib_keys_iterator_get_name(keys_itr)
        gribapi.grib_keys_iterator_delete(keys_itr)

    def _get_message_sections(self):
        """
        Groups keys in the GRIB message by containing section.

        Returns a dictionary of all sections in the message, where the value of
        each key is a :class:`collections.OrderedDict` object of key-value
        pairs for each key and associated value in the message section.

        .. seealso::
            The sections property (:meth:`~sections`).

        """
        sections = OrderedDict()
        # The first keys in a message are for the whole message and are
        # contained in section 0.
        section = new_section = 0
        # Use a `collections.OrderedDict` to retain key ordering.
        section_keys = OrderedDict()

        for key_name in self._get_message_keys():
            key_match = re.match(self._NEW_SECTION_KEY_MATCHER, key_name)

            if key_match is not None:
                new_section = int(key_match.group(1))
            # This key only shows up in section 8, which doesn't have a
            # `section8Length` coded key...
            elif key_name == '7777':
                new_section = 8

            if section != new_section:
                sections[section] = section_keys
                section_keys = OrderedDict()
            # This key is repeated in each section meaning that the last value
            # is always returned, so override the api-retrieved value.
            if key_name == 'numberOfSection':
                section_keys[key_name] = section
            else:
                # Leave out keys that have no associated value.
                # TODO should we instead keep it in and set value to None?
                try:
                    section_keys[key_name] = self._get_key_value(key_name)
                except KeyError:
                    continue
            section = new_section
        # Write the last section's dictionary to sections so it's not lost.
        sections[section] = section_keys
        return sections

    def _get_key_value(self, key):
        """
        Get the value associated with the given key in the GRIB message.

        Args:

        * key:
            The GRIB key to retrieve the value of.

        Returns the value associated with the requested key in the GRIB
        message.

        """
        res = None
        # See http://nullege.com/codes/search/gribapi.grib_get_values.
        try:
            if key in ['codedValues', 'values', 'pv']:
                res = gribapi.grib_get_array(self._message_id, key)
            elif key in ['typeOfFirstFixedSurface',
                         'typeOfSecondFixedSurface']:
                # By default these values are returned as unhelpful strings but
                # we can use int representation to compare against instead.
                res = gribapi.grib_get(self._message_id, key, type=int)
            else:
                res = gribapi.grib_get(self._message_id, key)
        # Deal with gribapi not differentiating between exception types.
        except gribapi.GribInternalError as e:
            # Catch the case of trying to retrieve, using `gribapi.grib_get`,
            # the value of an array key that is NOT in the list above .
            if e.msg == "Passed array is too small":
                res = gribapi.grib_get_array(self._message_id, key)
            # Catch cases where a computed key, e.g. `local 98.1` has no
            # associated value at all in the message.
            elif e.msg == "Key/value not found":
                msg = 'No value in message for key {!r}'
                raise KeyError(msg.format(key))
            else:
                raise e
        return res
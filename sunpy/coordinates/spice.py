"""
Experimental module to use the SkyCoord API for SPICE computations

.. warning::
    This module is under development, so may be subject to significant change.

The `SPICE <https://naif.jpl.nasa.gov/naif/>`__ observation geometry information
system is being increasingly used by space missions to describe the locations of
spacecraft and the time-varying orientations of reference frames.
While the `~spiceypy.spiceypy` package provides a Python interface for
performing SPICE computations, its API is very different from that of
`~astropy.coordinates.SkyCoord`.

This module "wraps" `~spiceypy.spiceypy` functionality so that relevant SPICE
computations can be accessed using the `~astropy.coordinates.SkyCoord` API.
When loading a set of kernels, a frame class and corresponding transformations
are created for each SPICE frame.  One can also query the location of a body
as computed via SPICE.

.. note::
    This module requires the optional dependency `~spiceypy.spiceypy` to be
    installed.

Notes
-----
* All transformations from one SPICE frame to another SPICE frame go through
  `~astropy.coordinates.ICRS` as the intermediate frame, even if the origin
  shift to/from the solar-system barycenter is unnatural.  This also means that
  it is not possible to transform a 2D coordinate between frames because there
  is always an origin shift.
* Transformations of velocities are not yet supported.
* There is currently no support for time arrays.
"""

try:
    import spiceypy
except ImportError:
    raise ImportError("This module requires the optional dependency `spiceypy`.")

import astropy.units as u
from astropy.coordinates import ICRS, SkyCoord, frame_transform_graph
from astropy.coordinates.representation import CartesianRepresentation
from astropy.coordinates.transformations import FunctionTransformWithFiniteDifference
from astropy.time import Time

from sunpy import log
from sunpy.coordinates import SunPyBaseCoordinateFrame
from sunpy.time import parse_time
from sunpy.time.time import _variables_for_parse_time_docstring
from sunpy.util.decorators import add_common_docstring

__all__ = ['get_body', 'initialize']


# Note that this epoch is very slightly different from the typical definition of J2000.0 (in TT)
_ET_REF_EPOCH = Time('J2000', scale='tdb')

_CLASS_TYPES = {2: 'PCK', 3: 'CK', 4: 'TK', 5: 'dynamic', 6: 'switch'}


# Defined for future use
class _SpiceBaseCoordinateFrame(SunPyBaseCoordinateFrame):
    pass


def _convert_to_et(time):
    return (time - _ET_REF_EPOCH).to_value('s')


def _install_frame(frame_id):
    frame_name = spiceypy.frmnam(frame_id)
    # TODO: Sanitize/escape the frame name of special characters

    frame_center, class_num, _ = spiceypy.frinfo(frame_id)
    log.info(f"Installing {frame_name} {_CLASS_TYPES[class_num]} frame ({frame_id}) "
             f"as 'spice_{frame_name}'")

    spice_frame = type(f"spice_{frame_name}",
                       (_SpiceBaseCoordinateFrame,),
                       {})
    # Force the capitalization
    # TODO: Consider adding the alias of all lowercase
    spice_frame.name = spice_frame.__name__

    # TODO: Figure out how to handle array time
    # TODO: Does it matter what time is used for J2000 ET?

    @frame_transform_graph.transform(FunctionTransformWithFiniteDifference, ICRS, spice_frame)
    def icrs_to_spice(from_icrs_coord, to_spice_frame):
        et = _convert_to_et(to_spice_frame.obstime)
        matrix = spiceypy.pxfrm2('J2000', frame_name, 0, et)
        icrs_offset = spiceypy.spkezp(frame_center, et, 'J2000', 'none', 0)[0] << u.km
        shifted_old_pos = from_icrs_coord.cartesian - CartesianRepresentation(icrs_offset)
        new_pos = shifted_old_pos.transform(matrix)
        return to_spice_frame.realize_frame(new_pos)

    @frame_transform_graph.transform(FunctionTransformWithFiniteDifference, spice_frame, ICRS)
    def spice_to_icrs(from_spice_coord, to_icrs_frame):
        et = _convert_to_et(from_spice_coord.obstime)
        matrix = spiceypy.pxfrm2(frame_name, 'J2000', et, 0)
        shifted_new_pos = from_spice_coord.cartesian.transform(matrix)
        icrs_offset = spiceypy.spkezp(frame_center, et, 'J2000', 'none', 0)[0] << u.km
        new_pos = shifted_new_pos + CartesianRepresentation(icrs_offset)
        return to_icrs_frame.realize_frame(new_pos)

    frame_transform_graph._add_merged_transform(spice_frame, ICRS, spice_frame)


# TODO: Support multiple calls to initialize()

def initialize(kernels):
    """
    Load one more more SPICE kernels and create corresponding frame classes.

    .. warning::
        As currently implemented, do not run this more than once per Python
        session.

    Parameters
    ----------
    kernels : `str`, `list` of `str`
         One or more SPICE kernel files

    Notes
    -----
    If a kernel file is a meta-kernel, make sure that the relative paths therein
    are correct for the current working directory, which may not be the same as the
    location of the meta-kernel file.
    """
    spiceypy.furnsh(kernels)

    for class_num in _CLASS_TYPES.keys():
        frames = spiceypy.kplfrm(class_num)
        for frame_id in frames:
            _install_frame(frame_id)


# TODO: Add support for array time
# TODO: Add support for light travel time correction

@add_common_docstring(**_variables_for_parse_time_docstring())
def get_body(body, time, *, spice_frame_name='J2000'):
    """
    Get the location of a body via SPICE.

    Parameters
    ----------
    body : `int`, `str`
        The NAIF body ID, or a string that is resolvable to a body ID
    time : {parse_time_types}
        Time to use in a parse_time-compatible format.
    spice_frame_name : `str`
        The SPICE frame to use for the returned coordinate.  Defaults to ``'J2000'``,
        which is equivalent to Astropy's `~astropy.coordinates.ICRS`.

    Notes
    -----
    No adjustment is made for light travel time to an observer.
    """
    body_id = body if isinstance(body, int) else spiceypy.bodn2c(body)
    obstime = parse_time(time)

    frame_center = spiceypy.frinfo(spiceypy.namfrm(spice_frame_name))[0]

    pos = spiceypy.spkezp(body_id,
                          _convert_to_et(obstime),
                          spice_frame_name,
                          'none',
                          frame_center)[0] << u.km

    frame_name = 'icrs' if spice_frame_name == 'J2000' else f"spice_{spice_frame_name}"

    return SkyCoord(CartesianRepresentation(pos), frame=frame_name, obstime=obstime)

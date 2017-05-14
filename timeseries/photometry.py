#
# Copyright (C) 2013 Patricio Rojo
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of version 2 of the GNU General
# Public License as published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor,
# Boston, MA  02110-1301, USA.
#
#

from __future__ import print_function
# noinspection PyUnresolvedReferences

from IPython.core.debugger import Tracer
import dataproc as dp
import scipy as sp
import scipy.optimize as op
import sys
import os.path
from .timeseries import TimeSeries
import matplotlib.pyplot as plt

import logging


class FilterMessage(logging.Filter):
    def add_needle(self, needle):
        if not hasattr(self, 'needle'):
            # noinspection PyAttributeOutsideInit
            self.needle = []
        self.needle.append(needle)
        return self

    def filter(self, record):
        for needle in self.needle:
            if needle in record.msg:
                return 0
        return 1

tmlogger = logging.getLogger('dataproc.timeseries')
for hnd in tmlogger.handlers:
    tmlogger.removeHandler(hnd)
for flt in tmlogger.filters:
    tmlogger.removeFilter(flt)
PROGRESS = 35
handler_console = logging.StreamHandler()
handler_console.setLevel(PROGRESS)
formatter_console = logging.Formatter('%(message)s')
handler_console.setFormatter(formatter_console)
tmlogger.addHandler(handler_console)


def _show_apertures(coords, aperture=None, sky=None,
                    axes=None, sk_color='w', ap_color='w',
                    n_points=30, alpha=0.5, labels=None, logger=None):

    if logger is None:
        logger = tmlogger

    if aperture is None:
        logger.warning("Using default aperture of 10 pixels")
        aperture = 10
    if sky is None:
        logger.warning("Using default sky of 15-20 pixels")
        sky = [15, 20]

    f, ax = dp.figaxes(axes, overwrite=True)
    for p in [pp for pp in ax.patches]:
        # noinspection PyArgumentList
        p.remove()
    for t in [tt for tt in ax.texts]:
        # noinspection PyArgumentList
        t.remove()

    if labels is None:
        labels = [''] * len(coords)

    for coo_xy, lab in zip(coords, labels):
        cx, cy = coo_xy
        theta = sp.linspace(0, 2 * sp.pi, n_points, endpoint=True)
        xs = cx + aperture * sp.cos(theta)
        ys = cy + aperture * sp.sin(theta)
        ax.fill(xs, ys,
                edgecolor=ap_color, color=ap_color,
                alpha=alpha)

        xs = cx + sp.outer(sky, sp.cos(theta))
        ys = cy + sp.outer(sky, sp.sin(theta))
        xs[1, :] = xs[1, ::-1]
        ys[1, :] = ys[1, ::-1]
        ax.fill(sp.ravel(xs), sp.ravel(ys),
                edgecolor=sk_color, color=sk_color,
                alpha=alpha)

        outer_sky = sky[1]
        ax.annotate(lab,
                    xy=(cx, cy + outer_sky),
                    xytext=(cx + 1 * outer_sky,
                            cy + 1.5 * outer_sky),
                    fontsize=20)


def _prep_offset(start, offsets, ignore):
    if ignore is None:
        ignore = []
    if offsets is None:
        offset_list = sp.zeros([1, 2])
    else:
        offset_list = sp.zeros([max(offsets.keys()) + 1, 2])
        for k, v in offsets.items():
            k -= start
            if k < 0:
                continue
            elif isinstance(v, (list, tuple)) and len(v) == 2:
                offset_list[k, :] = sp.array(v)
            elif isinstance(k, int) and v == 0:
                ignore.append(k)
            else:
                raise TypeError("Unrecognized type for offsets_xy {}."
                                " It can be either an xy-offset or 0 to indicate skipping".format(v))
    return ignore, offset_list


def _get_stamps(sci_files, target_coords_xy, stamp_rad, maxskip,
                mdark=None, mflat=None, labels=None, recenter=True,
                offsets_xy=None, logger=None, ignore=None, brightest=0,
                max_change_allowed=None, interactive=False, idx0=0,
                ccd_lims_xy=None):
    """

    :param sci_files:
    :type sci_files: AstroDir
    :param target_coords_xy: [[t1x, t1y], [t2x, t2y], ...]
    :param stamp_rad:
    :param ccd_lims_xy: [x1, x2, y1, y2] mean [x1:x2, y1:y2]
    :return:
    """

    if ignore is None:
        ignore = []

    skip_interactive = True

    ngood = len(sci_files)

    if max_change_allowed is None:
        max_change_allowed = stamp_rad

    if labels is None:
        labels = range(len(target_coords_xy))

    tmp = sp.zeros([len(sci_files), 2])
    if offsets_xy is None:
        offsets_xy = tmp
    elif len(offsets_xy) < len(sci_files):
        tmp[:len(offsets_xy), :] = offsets_xy
        offsets_xy = tmp
    del tmp

    if logger is None:
        logger = tmlogger

    skip_calib = False
    if mdark is None and mflat is None:
        skip_calib = True
    if mdark is None:
        mdark = 0.0
    if mflat is None:
        mflat = 1.0

    all_cubes = sp.zeros([len(target_coords_xy), ngood,
                          stamp_rad*2+1, stamp_rad*2+1])
    indexing = [0]*ngood
    center_xy = sp.zeros([len(target_coords_xy), ngood, 2])

    center_user_xy = [[xx, yy] for xx, yy in target_coords_xy]
    logger.log(PROGRESS,
               " Obtaining stamps for {} files: ".format(ngood))

    if interactive:
        # plt.ioff()
        f, ax = dp.figaxes()
        int_labels = ['']*len(labels)
        int_labels[brightest] = 'REF'
        flag = {}
        msg_filter = FilterMessage().add_needle('Using default ')
        logger.addFilter(msg_filter)
        print("Entering interactive mode:\n"
              " 'd'elete frame, re'c'enter apertures, flag '1'-'9', 'q'uit, "
              "keep 'g'oing until drift, <- prev frame, -> next frame")
        skip_interactive = False

    if ccd_lims_xy is None:
        ccd_lims_xy=[0, sci_files[0].shape[1], 0, sci_files[0].shape[0]]

    to_store = 0
    stat = 0
    previous_distance = None
    idx = 0
    try_dedrift = True
    n_files = len(sci_files)
    while idx < n_files:
        astrofile = sci_files[idx]
        off = offsets_xy[idx]
        d = astrofile.reader()
        d = d[ccd_lims_xy[2]:ccd_lims_xy[3], ccd_lims_xy[0]:ccd_lims_xy[1]]

        if isinstance(mdark, sp.ndarray):
            mdark = mdark[ccd_lims_xy[2]:ccd_lims_xy[3], ccd_lims_xy[0]:ccd_lims_xy[1]]

        if isinstance(mflat, sp.ndarray):
            mflat = mflat[ccd_lims_xy[2]:ccd_lims_xy[3], ccd_lims_xy[0]:ccd_lims_xy[1]]

        if not skip_calib:
            if astrofile.has_calib():
                logger.warning("Skipping calibration given to Photometry() because "
                               "calibration files\nwere included in AstroFile: {}".format(astrofile))
            d = (d - mdark) / mflat

        last_frame_coords_xy = []
        indexing[to_store] = idx
        if to_store:
            prev_center_xy = center_xy[:, to_store-1, :]
        else:
            prev_center_xy = center_user_xy

        for cube, trg_id in zip(all_cubes, range(len(labels))):
            cx, cy = prev_center_xy[trg_id][0]+off[0], prev_center_xy[trg_id][1]+off[1]
            if recenter:
                ncy, ncx = dp.subcentroid(d, [cy, cx], stamp_rad)
            else:
                ncy, ncx = cy, cx
            if ncy < 0 or ncx < 0 or ncy > d.shape[0] or ncx > d.shape[1]:
                # following is necessary to keep indexing that does not consider skipped bad frames
                af_names = [af.filename for af in sci_files]
                raise ValueError("Centroid for frame #{} falls outside data for target {}. "
                                 " Initial/final center was: [{:.2f}, {:.2f}]/[{:.2f}, {:.2f}]"
                                 " Offset: {}\n{}".format(af_names.index(astrofile.filename), labels[trg_id], cx, cy,
                                                          ncx, ncy, off, astrofile))
            cube[to_store] = dp.subarray(d, [ncy, ncx], stamp_rad, padding=True)
            center_xy[trg_id][to_store] = [ncx, ncy]

            if off.sum() > 0:
                stat |= 2

            skip = sp.sqrt((cx - ncx) ** 2 + (cy - ncy) ** 2)
            if skip > maxskip:
                stat |= 1
                if idx == 0:
                    logger.warning(
                        "Position of user coordinates adjusted by {skip:.1f} pixels"
                        " on first frame for target '{name}'".format(
                            skip=skip, name=labels[trg_id]))
                else:
                    logger.warning(
                        "Large jump of {skip:.1f} pixels for {name} has occurred on {frame}".format(
                            skip=skip, frame=astrofile, name=labels[trg_id]))

            last_frame_coords_xy.append([ncx, ncy])

        distance_to_brightest_vector = sp.array(last_frame_coords_xy) - sp.array(last_frame_coords_xy[brightest])
        distance_to_brightest = sp.sqrt((distance_to_brightest_vector*distance_to_brightest_vector).sum(1))
        if previous_distance is None:
            previous_distance = distance_to_brightest
        else:
            change = sp.absolute(distance_to_brightest - previous_distance)
            max_change = max(change)
            if max_change > max_change_allowed and idx not in ignore:
                if try_dedrift:
                    # todo: attempt an auto dedrift
                    raise NotImplementedError("Still need to add try_dedrift")
#                    try_dedrift = False
#                    continue
                else:
                    stat |= 4
                    logger.warning("Found star drifting from brightest by {:.1f} pixels between consecutive frames "
                                   "for target {}.".format(max_change,
                                                           labels[list(change).index(max_change)]))

        if interactive and (not skip_interactive or stat & 4):
            skip_interactive = False
            ax.cla()
            f.show()
            dp.imshowz(d, axes=ax, force_show=False)
            ax.set_ylabel('Frame #{}'.format(idx))
            curr_center_xy = center_xy[:, to_store, :]
            on_click_action = [1]

            def onclick(event):
                if event.inaxes != ax:
                    return
                if event.key == 'right':  # frame is good
                    pass
                elif event.key == 'q':
                    logger.log(PROGRESS, "User canceled stamp acquisition")
                    on_click_action[0] = -10
                elif event.key == 'left':  # previous frame
                    on_click_action[0] = -1
                elif event.key == 'd':  # ignore this frame
                    if idx in ignore:
                        ignore.pop(ignore.index(idx))
                    else:
                        ignore.append(idx)
                    on_click_action[0] = 0
                elif event.key == 'c':  # Fix the jump (offset)
                    prev_cnt_bright = center_xy[brightest][to_store-1]
                    offsets_xy[idx] = [event.xdata - prev_cnt_bright[0],
                                       event.ydata - prev_cnt_bright[1]]
                    on_click_action[0] = 0
                elif event.key == 'g':
                    on_click_action[0] = 10
                elif '9' >= event.key >= '1':  # flag the frame
                    d_idx = str(int(event.key))
                    if d_idx not in flag:
                        flag[d_idx] = []
                    flag[d_idx].append(idx)
                    return
                else:
                    return
                event.inaxes.figure.canvas.stop_event_loop()

            cid = f.canvas.mpl_connect('key_press_event', onclick)
            ap_color = sk_color = 'w'
            if idx in ignore:
                ap_color = sk_color = 'r'
            elif off.sum():
                ap_color = 'b'
            else:
                n_flag = sum([idx in fl for fl in flag])
                sk_color = n_flag and "gray{}0".format(9-n_flag) or 'w'
            _show_apertures(curr_center_xy, axes=ax, labels=int_labels,
                            sk_color=sk_color, ap_color=ap_color)
            f.show()
            f.canvas.start_event_loop(timeout=-1)
            f.canvas.mpl_disconnect(cid)

            if on_click_action[0] == -1:
                if not idx:
                    continue
                idx -= 1
                if idx not in ignore:
                    to_store -= 1
                continue
            elif on_click_action[0] == 0:
                continue
            elif on_click_action[0] == 10:  # continue until drift detected
                skip_interactive = True
            elif on_click_action[0] == -10:
                break

        if idx in ignore:
            stat |= 8
            idx += 1
            continue

        msg = ''
        if stat & 8:   # skipped at least 1
            msg += 'S'
        if stat & 2:   # offset applied
            msg += "O"
        if stat & 1:   # jump of a target
            msg += "J"
        if stat & 4:   # drift from brightest
            msg += "D"
        print('{}{}'.format(msg, idx % 10 == 9
                            and (idx % 100 == 99 and '=' or ':')
                            or '.'),
              end='')
        sys.stdout.flush()

        stat = 0
        to_store += 1
        idx += 1
        try_dedrift = False

    all_cubes = all_cubes[:, :to_store, :, :]
    indexing = indexing[:to_store]
    center_xy = center_xy[:, :to_store, :]
    print('')
    logger.log(PROGRESS, "Skipped {} flagged frames".format(ngood-to_store))

    if interactive:
        _show_apertures(curr_center_xy, axes=ax, labels=labels,
                        sk_color=sk_color, ap_color=ap_color)
        f.show()
        print ("offsets_xy = {", end='')
        for idx in range(len(offsets_xy)):
            if idx in ignore:
                print ("{:d}:0, ".format(idx+idx0), end='')
            elif offsets_xy[idx].sum() != 0:
                print ("{:d}: [{:.0f}, {:.0f}],\n              ".format(idx+idx0,
                                                                        offsets_xy[idx][0], offsets_xy[idx][1]),
                       end="")
        print("}")
        print ("flags = {")
        for i in range(1, 10):
            if str(i) in flag:
                print ("{:d}: {},".format(i, list(sp.array(list(set(flag[str(i)]))) + idx0)))
        print("}")
        logger.removeFilter(msg_filter)

    return indexing, all_cubes, center_xy


def _phot_error(phot, sky_std, n_pix_ap, n_pix_sky, gain=None, ron=None):
    """Calculates the photometry error

    :param phot: star flux
    :type phot: float
    :param n_pix_ap: number of pixels in the aperture
    :type n_pix_ap: int
    :param n_pix_sky: number of pixels in the sky annulus
    :type n_pix_sky: int
    :param gain: gain
    :type gain: float
    :param ron: read-out-noise
    :type ron: float (default value: None)
    :rtype: float
    """

    if ron is None:
        logging.warning("Photometric error calculated without read-out-noise")
        ron = 0.0

    if gain is None:
        logging.warning("Photometric error calculated without Gain")
        gain = 1.0

    var_flux = phot / gain
    var_sky = sky_std ** 2 * n_pix_ap * (1 + float(n_pix_ap) / n_pix_sky)

    var_total = var_sky + var_flux + ron * ron * n_pix_ap

    return sp.sqrt(var_total)


class Photometry:
    def __init__(self, sci_files, target_coords_xy, offsets_xy=None, idx0=0,
                 aperture=None, sky=None, mdark=None, mflat=None,
                 stamp_rad=30, outer_ap=1.2,
                 max_skip=8, max_counts=50000, recenter=True,
                 epoch='JD', labels=None, brightest=None,
                 deg=1, gain=None, ron=None,
                 logfile=None, ignore=None, extra=None,
                 interactive=False, ccd_lims_xy=None):
        """

        :param sci_files:
        :param target_coords_xy:
        :param aperture:
        :param sky:
        :param mdark:
        :param mflat:
        :param stamp_rad:
        :param offsets_xy:
        :param max_skip:
        :param max_counts:
        :param recenter:
        :param epoch:
        :param labels:
        :param brightest: index of star to use as position reference
        :param deg:
        :param gain:
        :param ron:
        :param logfile:
        :param ignore:
        :param ccd_lims_xy:
        """
        if isinstance(epoch, str):
            self.epoch = sci_files.getheaderval(epoch)
        elif hasattr(epoch, '__iter__'):
            self.epoch = epoch
        else:
            raise ValueError(
                "Epoch must be an array of dates in julian date, or a a header's keyword "
                "for the Julian date of the observation")

        # Following as default used to find the brightest star.
        # Otherwise, they are not used until .photometry(), which can override them
        if aperture is None:
            aperture = stamp_rad / 4.0
        if sky is None:
            sky = [int(stamp_rad*0.6), int(stamp_rad*0.8)]
        self.surrounding_ap_limit = outer_ap
        self.aperture = aperture
        self.sky = sky

        if extra is None:
            extra = []

        if not isinstance(idx0, int):
            raise TypeError("Initial index can only be an integer")

        self.deg = deg
        self.gain = gain
        self.ron = ron
        self.stamp_rad = stamp_rad
        self.max_counts = max_counts
        self.recenter = recenter
        self.max_skip = max_skip
        self.ccd_lims_xy = ccd_lims_xy

        if logfile is None:
            tmlogger.propagate = True
            self._logger = tmlogger
        else:
            tmlogger.propagate = False

            # use logger instance that includes filename to allow different instance of photometry
            # with different loggers as long as they use different files
            self._logger = logging.getLogger('dataproc.timeseries.{}'.
                                             format(os.path.basename(logfile).replace('.', '_')))
            self._logger.setLevel(logging.INFO)
            # in case of using same file name start new with loggers
            for hnd_tmp in self._logger.handlers:
                self._logger.removeHandler(hnd_tmp)

            handler = logging.FileHandler(logfile, 'w')
            formatter = logging.Formatter('%(asctime)s: %(name)s %(levelname)s: %(message)s')
            handler.setFormatter(formatter)
            handler.setLevel(logging.INFO)
            self._logger.addHandler(handler)

            print ("Detailed logging redirected to {}".format(logfile))
        self._logger.info("dataproc.timeseries.Photometry execution on: {}".format(sci_files))

        sci_files = dp.AstroDir(sci_files)

        ignore, offset_list = _prep_offset(idx0, offsets_xy, ignore)

        # label list
        if isinstance(target_coords_xy, dict):
            coords_user_xy = target_coords_xy.values()
            labels = target_coords_xy.keys()
        elif isinstance(target_coords_xy, (list, tuple)):
            coords_user_xy = list(target_coords_xy)
        else:
            raise TypeError("target_coords_xy type is invalid")

        try:
            if labels is None:
                labels = []
            nstars = len(target_coords_xy)
            if len(labels) > nstars:
                labels = labels[:nstars]
            elif len(labels) < nstars:
                labels = list(labels) + list(sp.arange(len(labels),
                                                       nstars).astype(str))
        except:
            raise ValueError("Coordinates of target stars need to be " +
                             "specified as dictionary or as a list of 2 elements, not: %s" %
                             (str(target_coords_xy),))

        self.labels = labels
        self.coords_user_xy = coords_user_xy

        # The following is to search for the brightest star... rough aperture photometry performed
        if brightest is None:
            flxs = []
            for trg in coords_user_xy:
                data = sci_files[0].reader()
                stamp = dp.subarray(data, dp.subcentroid(data, (trg[1], trg[0]), stamp_rad), stamp_rad)
                d = dp.radial(stamp, (stamp_rad, stamp_rad))
                sky_val = sp.median(stamp[(d < sky[1])*(d > sky[0])])
                flxs.append((stamp[d < aperture] - sky_val).sum())
            brightest = sp.argmax(flxs)
        self.brightest = brightest

        self._logger.log(PROGRESS,
                         " Initial guess received for {} targets, "
                         "reference brightest '{}'.".format(len(target_coords_xy),
                                                            self.labels[brightest]))
        tmlogger.info("Initial coordinates {}".format(", ".join(["%s %s" % (lab, coo)
                                                                 for lab, coo in zip(labels, coords_user_xy)])
                                                      ))

        indexing, self.sci_stamps, \
            self.coords_new_xy = _get_stamps(sci_files, self.coords_user_xy,
                                             self.stamp_rad, maxskip=max_skip,
                                             mdark=mdark, mflat=mflat,
                                             recenter=recenter,
                                             labels=labels,
                                             offsets_xy=offset_list,
                                             logger=self._logger,
                                             ignore=ignore,
                                             brightest=brightest,
                                             idx0=idx0,
                                             interactive=interactive,
                                             ccd_lims_xy=ccd_lims_xy)

        self.extra_header = extra
        self.mdark = mdark
        self.mflat = mflat

        # storing indexing only for those not ignored
        self.indexing = [idx + idx0 for idx in indexing]
        # Storing extras and frame_id with the original indexing.
        self.extras = {x: ['']*idx0 + list(v)
                       for x, v in zip(extra, zip(*sci_files.getheaderval(*extra,
                                                                          single_in_list=True)))}
        if idx0:
            raise NotImplementedError("HAving a value of idx0 uses lots of memory on dummy AstroDir "
                                      "to put before sci_files... needs to b investigated.  In the"
                                      "meantime idx0 is disabled.")
        self._astrodir = sci_files

    def set_max_counts(self, counts):
        self.max_counts = counts

    def photometry(self, aperture=None, sky=None,
                   deg=None, max_counts=None,
                   outer_ap=None):
        """

        :param aperture:
        :param sky:
        :param deg:
        :param max_counts:
        :param outer_ap:  Outer ring as a fraction of aperture, to report surrounding region
        :return:
        """
        if aperture is not None:
            self.aperture = aperture
        if sky is not None:
            self.sky = sky
        if deg is not None:
            self.deg = deg
        if max_counts is not None:
            self.set_max_counts(self.max_counts)
        if outer_ap is not None:
            self.surrounding_ap_limit = outer_ap

        if self.aperture is None or self.sky is None:
            raise ValueError("ERROR: aperture photometry parameters are incomplete. Either aperture "
                             "photometry radius or sky annulus were not giving. Please call photometry "
                             "with the following keywords: photometry(aperture=a, sky=s) or define aperture "
                             "and sky when initializing Photometry object.")

        if self.aperture > self.stamp_rad:
            raise ValueError("aperture photometry ({}) shouldn't be higher than radius of stamps ({})".format(self.aperture,
                                                                                                              self.stamp_rad))

        ts = self.cpu_phot()
        return ts

    def remove_from(self, idx):
        if not isinstance(idx, int):
            raise TypeError("idx can only be indexing")
        idx_skipping = self.indexing.index(idx)
        self.indexing = self.indexing[:idx_skipping]
        self.sci_stamps = self.sci_stamps[:, :idx_skipping, :, :]
        self.coords_new_xy = self.coords_new_xy[:, :idx_skipping, :]
        # self.frame_id = self.frame_id[:idx]
        for v in self.extras.keys():
            self.extras[v] = self.extras[v][:idx]
        self._astrodir = self._astrodir[:idx]

    def append(self, sci_files, offsets_xy=None, ignore=None):
        """
Adds more files to photometry
        :param offsets_xy:
        :param sci_files:
        :param ignore: Keeps the same zero from original serie
        """

        sci_files = dp.AstroDir(sci_files)
        start_frame = len(self._astrodir)

        if ignore is None:
            ignore = []
        extra = self.extra_header

        ignore, offset_list = _prep_offset(start_frame, offsets_xy, ignore)
        last_coords = [coords[-1] for coords in self.coords_new_xy]
        brightest = self.brightest

        indexing,\
            sci_stamps, coords_new_xy = _get_stamps(sci_files, last_coords,
                                                    self.stamp_rad, maxskip=self.max_skip,
                                                    mdark=self.mdark, mflat=self.mflat,
                                                    recenter=self.recenter,
                                                    labels=self.labels,
                                                    offsets_xy=offset_list,
                                                    logger=self._logger,
                                                    ignore=ignore,
                                                    brightest=brightest)
        self.sci_stamps = sp.concatenate((self.sci_stamps, sci_stamps), axis=1)
        self.coords_new_xy = sp.concatenate((self.coords_new_xy, coords_new_xy), axis=1)

        last_idx = self.indexing[-1]+1
        self.indexing += [idx + last_idx for idx in indexing]
        # noinspection PyUnusedLocal
        dummy = [self.extras[x].extend(v)
                 for x, v in zip(extra, zip(*sci_files.getheaderval(*extra,
                                                                    single_in_list=True)))]
        self._astrodir += sci_files

    def cpu_phot(self):
        """

        :return:
        """
        if isinstance(self.aperture, (list, tuple)):
            aperture = self.aperture
        else:
            aperture = [self.aperture]

        ns = len(self.indexing)
        nt = len(self.coords_new_xy)
        na = len(aperture)
        all_phot = sp.zeros([na, nt, ns])
        all_peak = sp.zeros([na, nt, ns])
        all_mom2 = sp.zeros([na, nt, ns])
        all_mom3 = sp.zeros([na, nt, ns])
        all_moma = sp.zeros([na, nt, ns])
        all_err = sp.zeros([na, nt, ns])
        all_fwhm = sp.zeros([nt, ns])
        all_excess = sp.zeros([na, nt, ns])

        print("Processing CPU photometry for {0} targets: ".format(len(self.sci_stamps)), end='')
        sys.stdout.flush()
        for label, target, centers_xy, t in zip(self.labels, self.sci_stamps,
                                                self.coords_new_xy, range(nt)):  # For each target

            for data, center_xy, non_ignore_idx, s in zip(target, centers_xy, self.indexing, range(ns)):
                cx, cy = center_xy

                # Stamps are already centered, only decimals could be different
                cnt_stamp = [self.stamp_rad + cy % 1, self.stamp_rad + cx % 1]

                # Preparing arrays for photometry
                d = dp.radial(data, cnt_stamp)
                dy, dx = data.shape
                y, x = sp.mgrid[-cnt_stamp[0]:dy - cnt_stamp[0], -cnt_stamp[1]:dx - cnt_stamp[1]]

                # Compute sky correction
                # Case 1: sky = [fit, map_of_sky_pixels]
                if isinstance(self.sky[0], sp.ndarray):
                    fit = self.sky[0]
                    idx = self.sky[1]

                # Case 2: sky = [inner_radius, outer_radius]
                else:
                    idx = (d > self.sky[0]) * (d < self.sky[1])
                    if self.deg == -1:
                        fit = sp.median(data[idx])
                    elif self.deg >= 0:
                        err_func = lambda coef, xx, yy, zz: (dp.bipol(coef, xx, yy) - zz).flatten()
                        coef0 = sp.zeros((self.deg, self.deg))
                        coef0[0, 0] = data[idx].mean()
                        fit, cov, info, mesg, success = op.leastsq(err_func, coef0.flatten(),
                                                                   args=(x[idx], y[idx], data[idx]), full_output=True)
                    else:
                        raise ValueError("invalid degree '{}' to fit sky".format(self.deg))

                # Apply sky subtraction
                n_pix_sky = idx.sum()
                if self.deg == -1:
                    sky_fit = fit
                elif self.deg >= 0:
                    sky_fit = dp.bipol(fit, x, y)
                else:
                    raise ValueError("invalid degree '{}' to fit sky".format(self.deg))

                sky_std = (data - sky_fit)[idx].std()
                res = data - sky_fit  # minus sky

                # Following to compute FWHM by fitting gaussian
                res2 = res[d < self.sky[1]].ravel()
                d2 = d[d < self.sky[1]].ravel()
                to_fit = lambda dd, h, sigma: h * dp.gauss(dd, sigma, ndim=1)
                try:
                    sig, cov = op.curve_fit(to_fit, d2, res2, sigma=1 / sp.sqrt(sp.absolute(res2)),
                                            p0=[max(res2), 3])
                except RuntimeError:
                    sig = sp.array([0, 0, 0])
                fwhm_g = 2.355 * sig[1]
                all_fwhm[t, s] = fwhm_g

                # now photometry
                for ap_idx in range(len(aperture)):
                    ap = aperture[ap_idx]
                    psf = res[d < ap]
                    if (psf > self.max_counts).any():
                        logging.warning("Object {} on frame #{} has counts above the "
                                        "threshold ({})".format(label,
                                                                self.indexing[non_ignore_idx],
                                                                self.max_counts))
                    all_phot[ap_idx, t, s] = phot = float(psf.sum())
                    all_peak[ap_idx, t, s] = float(psf.max())
                    all_excess[ap_idx, t, s] = float(res[(d < (self.surrounding_ap_limit*aperture[ap_idx])) *
                                                              (d > aperture[ap_idx])].sum())
                    dx = x-cnt_stamp[1]
                    dy = y-cnt_stamp[0]
                    res_pos = res * (res > 0)

                    skew_x = sp.sum((res_pos*(dx**3))[d < ap])
                    skew_y = sp.sum((res_pos*(dy**3))[d < ap])
                    all_mom2[ap_idx, t, s] = float(sp.sum((res_pos * (d ** 2))[d < ap]))
                    all_mom3[ap_idx, t, s] = sp.sqrt(skew_x**2 + skew_y**2)
                    all_moma[ap_idx, t, s] = sp.arctan2(skew_y, skew_x)

                    # now the error
                    if self.gain is None:
                        error = None
                    else:
                        n_pix_ap = (d < aperture[ap_idx]).sum()
                        error = _phot_error(phot, sky_std, n_pix_ap, n_pix_sky, self.gain, ron=self.ron)
                    all_err[ap_idx, t, s] = error

            print('X', end='')
            sys.stdout.flush()

        print('')
        errors = {}
        information = {'centers_xy': self.coords_new_xy, 'fwhm': all_fwhm}
        for ap in aperture:
            ap_idx = aperture.index(ap)
            information['flux_ap{:d}'.format(int(ap))] = all_phot[ap_idx, :, :]
            information['mom2_mag_ap{:d}'.format(int(ap), )] = all_mom2[ap_idx, :, :]
            information['mom3_mag_ap{:d}'.format(int(ap), )] = all_mom3[ap_idx, :, :]
            information['mom3_ang_ap{:d}'.format(int(ap), )] = all_moma[ap_idx, :, :]
            information['peak_ap{:d}'.format(int(ap))] = all_phot[ap_idx, :, :]
            information['excess_ap{:d}'.format(int(ap))] = all_excess[ap_idx, :, :]
            errors['flux_ap{:d}'.format(int(ap))] = all_err[ap_idx, :, :]

        # todo: make a nicer epoch passing
        return TimeSeries(information,
                          errors,
                          labels=self.labels,
                          epoch=[self.epoch[e] for e in range(len(self.epoch)) if e in self.indexing],
                          default_info='flux_ap{:d}'.format(int(aperture[0])),
                          )

    def last_coordinates(self, pos=None):
        """
Returns a dictionary with latest positions... useful if continued on a separate object
        :return:
        """
        if pos is None:
            ret_idx = -1
        else:
            ret_idx = self.indexing.index(pos)
        return {self.labels[k]: list(self.coords_new_xy[k, ret_idx, :].astype(int))
                for k in range(len(self.labels))}

    def plot_radialprofile(self, targets=None, xlim=None, axes=1,
                           legend_size=None, frame=0,
                           recenter=True, save=None, overwrite=False):
        """Plot Radial Profile from data using radialprofile() function
        :param targets: Target specification for re-centering. Either an integer for specific target.
        :type targets: integer/string
        :param xlim:
        :param axes:
        :param legend_size:
        :param frame:
        :param recenter:
    """

        colors = ['kx', 'rx', 'bx', 'gx', 'k^', 'r^', 'b^', 'g^', 'ko', 'ro', 'bo', 'go']
        fig, ax = dp.figaxes(axes, overwrite=overwrite)

        ax.cla()
        ax.set_title('Radial profile')
        ax.set_xlabel('Distance (in pixels)')
        ax.set_ylabel('ADU')
        if targets is None:
            targets = self.labels
        elif isinstance(targets, str):
            targets = [targets]
        elif isinstance(targets, (list, tuple)) and isinstance(targets[0], (int, )):
            targets = [self.labels[a] for a in targets]
        elif isinstance(targets, int):
            targets = [self.labels[targets]]

        stamp_rad = self.stamp_rad

        for stamp, coords_xy, color, lab in zip(self.sci_stamps, self.coords_new_xy,
                                                colors, self.labels):
            if lab in targets:
                cx, cy = coords_xy[frame]
                distance, value, center = dp.radial_profile(stamp[frame],
                                                            [stamp_rad+cx % 1, stamp_rad+cy % 1],
                                                            stamp_rad=stamp_rad,
                                                            recenter=recenter)
                ax.plot(distance, value, color,
                        label="%s: (%.1f, %.1f) -> (%.1f, %.1f)" % (lab,
                                                                    coords_xy[frame][0],
                                                                    coords_xy[frame][1],
                                                                    coords_xy[frame][0]-stamp_rad+center[0],
                                                                    coords_xy[frame][1]-stamp_rad+center[1]),
                        )
        prop = {}
        if legend_size is not None:
            prop['size'] = legend_size
        ax.legend(loc=1, prop=prop)

        if xlim is not None:
            if isinstance(xlim, (int, float)):
                ax.set_xlim([0, xlim])
            else:
                ax.set_xlim(xlim)

        if save is not None:
            plt.savefig(save)

        else:
            plt.show()

    def showstamp(self, target=None, stamp_rad=None, axes=None,
                  first=0, last=-1, n_show=None, ncol=None, annotate=True,
                  imshow=None, save=None, overwrite=False):
        """Show the star at the same position for the different frames

        :param imshow:
        :param axes:
        :param n_show: indicates the number of figures to present. It overwrites the value of last
        :param stamp_rad:
        :param annotate:
        :param target: None for the first key()
        :param first: First frame to show
        :param last: Last frame to show. It can be onPython negative format
        :param ncol: Number of columns
"""
        if target is None:
            target = 0
        elif isinstance(target, str):
            target = self.labels.index(target)

        if n_show is not None:
            last = first + n_show

        # change first and last to skipped indexing
        first = list(sp.array(self.indexing) >= first).index(True)
        if last < 0:
            last += len(self._astrodir)
        try:
            last = list(sp.array(self.indexing) >= last).index(True)
        except ValueError:
            last = len(self.indexing)-1
        n_images = last - first + 1

        if stamp_rad is None or stamp_rad > self.stamp_rad:
            stamp_rad = self.stamp_rad

        if ncol is None:
            ncol = int(sp.sqrt(n_images)*1.3)
        nrow = int(sp.ceil(float(n_images) / ncol))

        stamp_d = 2*stamp_rad+1
        array = sp.zeros([nrow*(stamp_d+2), ncol*(stamp_rad*2+3)])
        for data, idx in zip(self.sci_stamps[target][first:last+1],
                             range(first, last+1)):
            pos_idx = idx - first
            xpos = 1+(pos_idx % ncol)*(stamp_d+2)
            ypos = 1+(pos_idx//ncol)*(stamp_d+2)
            array[ypos:ypos+stamp_d, xpos: xpos+stamp_d] = data

        f_stamp, ax_stamp = dp.figaxes(axes, overwrite=overwrite)
        dp.imshowz(array, axes=ax_stamp, force_show=False)
        if annotate:
            for idx in range(first, last+1):
                pos_idx = idx - first
                xpos = 1 + stamp_rad/5 + (pos_idx % ncol) * (stamp_d + 2)
                ypos = 1 + stamp_rad/10 + (pos_idx // ncol) * (stamp_d + 2)
                plt.text(xpos, ypos, self.indexing[idx])

        if imshow is not None:
            def onclick(event):
                if event.inaxes != ax_stamp:
                    return
                xx, yy = event.xdata, event.ydata
                goto_idx = self.indexing[first:last][int(ncol*(yy // (stamp_d+2)) + xx//(stamp_d+2))]
                ax_show.cla()
                f_show.show()
                self.imshowz(goto_idx, axes=ax_show)
            f_show, ax_show = dp.figaxes(imshow)

            # noinspection PyUnusedLocal
            dummy = ax_stamp.figure.canvas.mpl_connect('button_press_event', onclick)
        if save is not None:
            plt.savefig(save)
        else:
            plt.show()

    def plot_drift(self, target=None, axes=None):
        """

        :param target:
        :param axes:
        """
        colors = ['k-', 'r-', 'b-', 'g-', 'k--', 'r--', 'b--', 'g--', 'k:', 'r:', 'b:', 'g:']
        fig, ax = dp.figaxes(axes)

        if target is None:
            labels = self.labels
            coords_xy = self.coords_new_xy
        elif isinstance(target, int):
            labels = [self.labels[target]]
            coords_xy = [self.coords_new_xy[target]]
        elif isinstance(target, str):
            labels = [target]
            coords_xy = [self.coords_new_xy[self.labels.index(target)]]
        else:
            raise TypeError("target type not identified")

        for label, coord_xy, color in zip(labels, coords_xy, colors):
            xx, yy = coord_xy.transpose()
            ax.plot(xx-xx[0], yy-yy[0], color, label=label)

        ax.legend(bbox_to_anchor=(0., 1.02, 1., .302), loc=3,
                  ncol=int(len(labels) // 2), mode="expand", borderaxespad=0.,
                  prop={'size': 8})
        fig.show()

    def plot_extra(self, x_id=None, axes=None):
        """

        :param x_id:
        :param axes:
        """
        fig, ax, x = dp.figaxes_xdate(self.epoch, axes=axes)

        ax.plot(x, self.extras[x_id])
        ax.set_xlabel("Epoch")
        ax.set_ylabel(x_id)

    def imshowz(self, frame=0,
                ap_color='w', sk_color='LightCyan',
                alpha=0.6, axes=None, reference=None,
                annotate=True, cnt=None, interactive=True,
                save=None, overwrite=False, **kwargs):

        """

        :param interactive:
        :param reference:
        :param frame:
        :param ap_color:
        :param sk_color:
        :param alpha:
        :param axes:
        :param annotate:
        :param cnt:  It can be an XY tuple or a str to identify an
          specific target position at the required frame
        :param kwargs:
        """
        f, ax = dp.figaxes(axes, overwrite=overwrite)
        ax.cla()
        d = self._astrodir[frame]
        dp.imshowz(d, axes=ax,
                   force_show=False, **kwargs)

        if reference is None:
            reference = frame
        elif reference < 0:
            reference += frame
        reference_ignore = self.indexing.index(reference)

        def coords_n_cnt(ref, trg):
            coord = zip(*self.coords_new_xy)[ref]
            if trg is None:
                cnt_label = 'origin'
                cnt_coord_xy = [0, 0]
            elif isinstance(trg, int):
                cnt_label = self.labels[trg]
                cnt_coord_xy = coord[trg]
            elif isinstance(trg, str):
                cnt_label = trg
                cnt_coord_xy = coord[self.labels.index(trg)]
            else:
                raise TypeError("cnt has to be label identification")
            return coord, cnt_label, cnt_coord_xy

        coords, ref_label, ref_xy = coords_n_cnt(reference_ignore, cnt)

        # noinspection PyDefaultArgument
        def _onkey(event, store=[], ref_input=[]):
            if event.inaxes != ax:
                return
            xx, yy = event.xdata, event.ydata
            ref = reference_ignore
            cnt_tmp = cnt
            if len(store) == 0:
                ref_input.append(False)
                store.extend([ref_label, ref_xy])
            if ref_input[0]:   # Having to grab one number at a time
                if len(event.key) == 1 and (ord('0') <= ord(event.key) <= ord('9')):
                    for i in range(len(ref_input)):
                        if ref_input[i] is False:
                            ref_input[i] = event.key
                            break
                    else:
                        ref_input.append(event.key)
                    event.inaxes.set_xlabel("Input Reference: {}".format("".join([a for a
                                                                                  in ref_input
                                                                                  if a is not True])))
                    event.inaxes.figure.show()
                    return
                elif event.key == 'enter':
                    ref = self.indexing.index(int("".join(ref_input[1:])))
                    while ref_input.pop() is not True:
                        pass
                    ref_input.append(False)
                    event.inaxes.set_xlabel("")
                else:
                    return
                event.inaxes.figure.show()
            elif event.key == 'c':
                dist = sp.sqrt(((sp.array(coords)-sp.array([xx, yy])[None, :])**2).sum(1))
                cnt_tmp = self.labels[sp.argmin(dist)]
            elif event.key == 'o':
                print(store)
                print("Offset to {}: {}, {}".format(store[0], xx-store[1][0], yy-store[1][1]))
                return
            elif event.key == 'r':
                ref_input[0] = True
                event.inaxes.set_xlabel("Input Reference: ")
                event.inaxes.figure.show()
                return
            else:
                return
            n_coords, n_ref_label, n_ref_xy = coords_n_cnt(ref, cnt_tmp)
            # noinspection PyUnusedLocal
            store[:] = [n_ref_label, n_ref_xy]
            _show_apertures(coords, aperture=self.aperture, sky=self.sky,
                            axes=ax, labels=annotate and self.labels or None,
                            sk_color=sk_color, ap_color=ap_color, alpha=alpha)
            ax.set_ylabel("Frame #{}{}".format(frame, reference != frame
                                               and ", apertures from #{}".format(reference)
                                               or ""))

        def _onclick(event):
            if event.inaxes != ax:
                return
            xx, yy = event.xdata - ref_xy[0], event.ydata - ref_xy[1]
            print ("\nFrame #{} (X, Y, Flux) = ({:.1f}, {:.1f}{})]".format(frame,
                                                                           event.xdata, event.ydata,
                                                                           d[event.ydata, event.xdata]))
            print ("Distance to '{}'{}: (x,y,r) = ({:.1f}, {:.1f}, {:.1f})".format(ref_label,
                                                                                   reference != frame
                                                                                   and " on frame #{}".format(reference)
                                                                                   or "",
                                                                                   xx, yy,
                                                                                   sp.sqrt(xx*xx+yy*yy),
                                                                                   ))
        if interactive:
            f.canvas.mpl_connect('button_press_event', _onclick)
            f.canvas.mpl_connect('key_press_event', _onkey)

        _show_apertures(coords, aperture=self.aperture, sky=self.sky,
                        axes=ax, labels=annotate and self.labels or None,
                        sk_color=sk_color, ap_color=ap_color, alpha=alpha)
        ax.set_ylabel("Frame #{}{}".format(frame, reference != frame
                                           and ", apertures from #{}".format(reference)
                                           or ""))
        if save is not None:
            plt.savefig(save)
        else:
            plt.show()
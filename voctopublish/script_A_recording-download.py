#!/usr/bin/python3
#    Copyright (C) 2017  derpeter
#    derpeter@berlin.ccc.de
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.

import configparser
import logging
import os
import shutil
import socket
import subprocess
import sys
import urllib.request

import api_client.twitter_client as twitter
from api_client.c3tt_rpc_client import C3TTClient
from api_client.voctoweb_client import VoctowebClient
from api_client.youtube_client import YoutubeAPI
from model.ticket_module import PublishingTicket, RecordingTicket


class Worker:
    """
    This is the main class for the Voctopublish application
    It is meant to be used with the c3tt ticket tracker
    """

    def __init__(self):
        raise Exception(
            'download worker uses a bunch of no-longer-existing options, please fix and remove this exception'
        )

        # load config
        if not os.path.exists('client.conf'):
            raise IOError("Error: config file not found")

        self.config = configparser.ConfigParser()
        self.config.read('client.conf')

        # set up logging
        logging.addLevelName(
            logging.WARNING,
            "\033[1;33m%s\033[1;0m" % logging.getLevelName(logging.WARNING),
        )
        logging.addLevelName(
            logging.ERROR, "\033[1;41m%s\033[1;0m" % logging.getLevelName(logging.ERROR)
        )
        logging.addLevelName(
            logging.INFO, "\033[1;32m%s\033[1;0m" % logging.getLevelName(logging.INFO)
        )
        logging.addLevelName(
            logging.DEBUG, "\033[1;85m%s\033[1;0m" % logging.getLevelName(logging.DEBUG)
        )

        self.logger = logging.getLogger()

        ch = logging.StreamHandler(sys.stdout)
        if self.config['general']['debug']:
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s {%(filename)s:%(lineno)d} %(message)s'
            )
        else:
            formatter = logging.Formatter('%(asctime)s - %(message)s')

        ch.setFormatter(formatter)
        self.logger.addHandler(ch)
        self.logger.setLevel(logging.DEBUG)

        level = self.config['general']['debug']
        if level == 'info':
            self.logger.setLevel(logging.INFO)
        elif level == 'warning':
            self.logger.setLevel(logging.WARNING)
        elif level == 'error':
            self.logger.setLevel(logging.ERROR)
        elif level == 'debug':
            self.logger.setLevel(logging.DEBUG)

        self.worker_type = self.config['general']['worker_type']

        # get a ticket from the tracker and initialize the ticket object
        if self.config['C3Tracker']['host'] == "None":
            self.host = socket.getfqdn()
        else:
            self.host = self.config['C3Tracker']['host']

        self.from_state = self.config['C3Tracker']['ticket_type']
        self.to_state = self.config['C3Tracker']['to_state']

        try:
            self.c3tt = C3TTClient(
                self.config['C3Tracker']['url'],
                self.config['C3Tracker']['group'],
                self.host,
                self.config['C3Tracker']['secret'],
            )
        except Exception as e_:
            raise PublisherException(
                'Config parameter missing or empty, please check config'
            ) from e_

        try:
            self.ticket = self._get_ticket_from_tracker()
        except Exception as e_:
            raise PublisherException('Could not get ticket from tracker') from e_

        if not self.ticket:
            return

        if self.from_state == 'encoding' and self.to_state == 'releasing':
            # todo this should in the publish function for better error handling
            # voctoweb
            if (
                self.ticket.profile_media_enable == 'yes'
                and self.ticket.media_enable == 'yes'
            ):
                api_url = self.config['voctoweb']['api_url']
                api_key = self.config['voctoweb']['api_key']
                self.vw = VoctowebClient(self.ticket, api_key, api_url)

            # YouTube
            if (
                self.ticket.profile_youtube_enable == 'yes'
                and self.ticket.youtube_enable == 'yes'
            ):
                self.yt = YoutubeAPI(self.ticket, self.config)

            # twitter
            if self.ticket.twitter_enable == 'yes':
                self.token = self.config['twitter']['token']
                self.token_secret = self.config['twitter']['token_secret']
                self.consumer_key = self.config['twitter']['consumer_key']
                self.consumer_secret = self.config['twitter']['consumer_secret']

    def publish(self):
        """
        Decide based on the information provided by the tracker where to publish.
        """
        # check source file and filesystem permissions
        if not os.path.isfile(self.ticket.publishing_path + self.ticket.local_filename):
            raise IOError(
                'Source file does not exist (%s)'
                % (self.ticket.publishing_path + self.ticket.local_filename)
            )
        if not os.path.exists(self.ticket.publishing_path):
            raise IOError(
                "Output path does not exist (%s)" % self.ticket.publishing_path
            )
        else:
            if not os.access(self.ticket.publishing_path, os.W_OK):
                raise IOError(
                    "Output path is not writable (%s)" % self.ticket.publishing_path
                )

        # Voctoweb
        logging.debug(
            'encoding profile media flag: '
            + self.ticket.profile_media_enable
            + " project media flag: "
            + self.ticket.media_enable
        )

        if (
            self.ticket.profile_media_enable == "yes"
            and self.ticket.media_enable == "yes"
        ):
            self._publish_to_voctoweb()

        # YouTube
        logging.debug(
            "encoding profile youtube flag: "
            + self.ticket.profile_youtube_enable
            + ' project youtube flag: '
            + self.ticket.youtube_enable
        )

        if (
            self.ticket.profile_youtube_enable == 'yes'
            and self.ticket.youtube_enable == 'yes'
            and not self.ticket.has_youtube_url
        ):
            self._publish_to_youtube()

        self.c3tt.set_ticket_done()

        # Twitter
        if self.ticket.twitter_enable == 'yes':
            twitter.send_tweet(
                self.ticket,
                self.token,
                self.token_secret,
                self.consumer_key,
                self.consumer_secret,
            )

    def download(self):
        """
        download or copy a file for processing
        :return:
        """
        # if its an URL it probably will start with http ....
        if self.ticket.download_url.startswith(
            'http'
        ) or self.ticket.download_url.startswith('ftp'):
            self._download_file()
        else:
            self._copy_file()

        # set recording language todo multilang
        try:
            self.c3tt.set_ticket_properties({'Record.Language': self.ticket.language})
        except AttributeError as err_:
            self.c3tt.set_ticket_failed(
                'unknown language please set language in the recording ticket to proceed'
            )
            logging.error(
                'unknown language please set language in the recording ticket to proceed'
            )

        # tell the tracker that we finished the import
        self.c3tt.set_ticket_done()

    def _get_ticket_from_tracker(self):
        """
        Request the next unassigned ticket for the configured states
        """
        logging.info('requesting ticket from tracker')

        ticket_id = self.c3tt.assign_next_unassigned_for_state(
            self.from_state, self.to_state
        )
        if ticket_id:
            logging.info("Ticket ID:" + str(ticket_id))
            tracker_ticket = self.c3tt.get_ticket_properties()
            logging.debug("Ticket: " + str(tracker_ticket))

            if self.worker_type == 'recording':
                t = RecordingTicket(tracker_ticket, ticket_id)
            elif self.worker_type == 'Voctopublish':
                t = PublishingTicket(tracker_ticket, ticket_id)
            else:
                raise PublisherException('unknown ticket typ in configured')

        else:
            logging.info("No ticket to publish, exiting")
            return None

        return t

    def _publish_to_voctoweb(self):
        """
        Create a event on an voctomix instance. This includes creating a event and a recording for each media file.
        This methods also start the scp uploads and handles multi language audio
        """
        logging.info("Voctopublish to voctoweb")

        if self.ticket.master:
            # if this is master ticket we need to check if we need to create an event on voctoweb
            logging.debug('this is a master ticket')
            if self.ticket.recording_id:
                logging.debug('ticket has a recording id')
                # ticket has an recording id. We assume the event exists on media
                # todo ask media api if event exists
            else:
                # ticket has no recording id therefore we create the event on voctoweb
                r = self.vw.create_event()
                if r.status_code in [200, 201]:
                    logging.info("new event created")
                    # generate the thumbnails for video releases (will not overwrite existing thumbs)
                    if self.ticket.mime_type.startswith('video'):
                        # if not os.path.isfile(self.ticket.publishing_path + self.ticket.local_filename_base + ".jpg"):
                        self.vw.generate_thumbs()
                        self.vw.upload_thumbs()
                        # else:
                        #     logging.info("thumbs exist. skipping")

                elif r.status_code == 422:
                    # If this happens tracker and voctoweb are out of sync regarding the recording id
                    logging.warning("event already exists => publish")
                else:
                    raise RuntimeError(
                        (
                            "ERROR: Could not add event: "
                            + str(r.status_code)
                            + " "
                            + r.text
                        )
                    )

                # in case of a multi language release we create here the single language files
                if len(self.ticket.languages) > 1:
                    logging.info(
                        'remuxing multi-language video into single audio files'
                    )
                    self._mux_to_single_language()

        # set hq filed based on ticket encoding profile slug
        if 'hd' in self.ticket.profile_slug:
            hq = True
        else:
            hq = False

        # if multi language release we don't want to set the html5 flag for the master
        if len(self.ticket.languages) > 1:
            html5 = False
        else:
            html5 = True

        if self.ticket.mime_type.startswith('audio'):
            # probably deprecated, just kept for reference
            # if we have the language index we use it else we assume its 0
            # if self.ticket.language_index and len(self.ticket.language_index) > 0:
            #    index = int(self.ticket.language_index)
            # else:
            #    index = 0
            # filename = self.ticket.language_template % self.ticket.languages[index] + '.' + self.ticket.profile_extension
            filename = (
                self.ticket.language_template % self.ticket.languages[0]
                + '.'
                + self.ticket.profile_extension
            )
            language = self.ticket.languages[0]
        else:
            filename = self.ticket.filename
            language = self.ticket.language

        self.vw.upload_file(self.ticket.local_filename, filename, self.ticket.folder)

        recording_id = self.vw.create_recording(
            self.ticket.local_filename,
            filename,
            self.ticket.folder,
            language,
            hq,
            html5,
        )

        self.c3tt.set_ticket_properties({'Voctoweb.RecordingId.Master': recording_id})

    def _mux_to_single_language(self):
        """
        Mux a multi language video file into multiple single language video files.
        This is only implemented for the h264 hd files as we only do it for them
        :return:
        """
        logging.debug('Languages: ' + str(self.ticket.languages))
        for key in self.ticket.languages:
            out_filename = (
                self.ticket.fahrplan_id
                + "-"
                + self.ticket.profile_slug
                + "-audio"
                + str(key)
                + "."
                + self.ticket.profile_extension
            )
            out_path = os.path.join(self.ticket.publishing_path, out_filename)
            filename = (
                self.ticket.language_template % self.ticket.languages[key]
                + '.'
                + self.ticket.profile_extension
            )

            logging.info('remuxing ' + self.ticket.local_filename + ' to ' + out_path)

            try:
                subprocess.call(
                    [
                        'ffmpeg',
                        '-y',
                        '-v',
                        'warning',
                        '-nostdin',
                        '-i',
                        os.path.join(
                            self.ticket.publishing_path, self.ticket.local_filename
                        ),
                        '-map',
                        '0:0',
                        '-map',
                        '0:a:' + str(key),
                        '-c',
                        'copy',
                        '-movflags',
                        'faststart',
                        out_path,
                    ]
                )
            except Exception as e_:
                raise PublisherException(
                    'error remuxing ' + self.ticket.local_filename + ' to ' + out_path
                ) from e_

            try:
                self.vw.upload_file(out_path, filename, self.ticket.folder)
            except Exception as e_:
                raise PublisherException('error uploading ' + out_path) from e_

            try:
                self.vw.create_recording(
                    out_filename,
                    filename,
                    self.ticket.folder,
                    str(self.ticket.languages[key]),
                    True,
                    True,
                )
            except Exception as e_:
                raise PublisherException('creating recording ' + out_path) from e_

    def _publish_to_youtube(self):
        """
        Publish the file to YouTube.
        """
        logging.debug("Voctopublish to youtube")
        youtube_urls = self.yt.publish()
        props = {}
        for i, youtubeUrl in enumerate(youtube_urls):
            props['YouTube.Url' + str(i)] = youtubeUrl

        self.c3tt.set_ticket_properties(props)

    def _copy_file(self):
        """
        copy a file from a local folder to the fake fuse and name it uncut.ts
        this hack to import files not produced with the tracker into the workflow to publish it on the voctoweb / youtube
        :return:
        """
        path = os.path.join(
            self.ticket.fuse_path, self.ticket.room, self.ticket.fahrplan_id
        )
        file = os.path.join(path, 'uncut.ts')
        logging.info(
            'Copying input file from: ' + self.ticket.download_url + ' to ' + file
        )
        if not os.path.exists(path):
            try:
                os.makedirs(path)
            except Exception as e:
                logging.error(e)
                logging.exception(e)
                raise PublisherException(e)

        if os.path.exists(file):
            # todo think about rereleasing here
            logging.warning('video file already exists, please remove file')
            raise PublisherException('video file already exists, please remove file')

        try:
            shutil.copyfile(self.ticket.download_url, file)
        except IOError as e_:
            raise PublisherException(e_)

    def _download_file(self):
        """
        download a file from an http / https / ftp URL an place it as a uncut.ts in the fuse folder.
        this hack to import files not produced with the tracker into the workflow to publish it on the voctoweb / youtube
        :return:
        """
        # we name our input video file uncut ts so tracker will find it. This is not the nicest way to go
        # TODO find a better integration in to the pipeline
        path = os.path.join(
            self.ticket.fuse_path, self.ticket.room, self.ticket.fahrplan_id
        )
        file = os.path.join(path, 'uncut.ts')
        logging.info(
            'Downloading input file from: ' + self.ticket.download_url + ' to ' + file
        )

        if not os.path.exists(path):
            try:
                os.makedirs(path)
            except Exception as e:
                logging.error(e)
                logging.exception(e)
                raise PublisherException(e)

        if os.path.exists(file):
            # todo think about rereleasing here
            logging.warning(
                'video file "' + path + '" already exists, please remove file'
            )
            raise PublisherException('video file already exists, please remove file')

        with open(file, 'wb') as fh:
            url = self.ticket.download_url
            url_decoded = urllib.parse.unquote(url)
            # if the unquoted URL has the same length as the input it was not url encoded
            logging.debug(
                "Test if url is encoded, len url: "
                + str(len(url))
                + " len url decoded: "
                + str(len(url_decoded))
            )
            if len(url) != len(url_decoded):
                # if it was encoded we decode it before passing it further
                logging.debug(
                    "URL: " + url + " was url encoded, decoding it before processing"
                )
                url = url_decoded
            logging.debug("Downloading file from: " + url)
            with urllib.request.urlopen(urllib.parse.quote(url, safe=':/')) as df:
                # original version tried to write whole file to ram and ran aut of memory
                # read in 16 kB chunks instead
                while True:
                    chunk = df.read(16384)
                    if not chunk:
                        break
                    fh.write(chunk)


class PublisherException(Exception):
    pass


if __name__ == '__main__':
    try:
        w = Worker()
    except Exception as e:
        logging.error(e)
        logging.exception(e)
        sys.exit(-1)

    if w.ticket:
        if w.worker_type == 'releasing':
            try:
                w.publish()
            except Exception as e:
                w.c3tt.set_ticket_failed(str(e))
                logging.exception(e)
                sys.exit(-1)
        elif w.worker_type == 'recording':
            try:
                w.download()
            except Exception as e:
                w.c3tt.set_ticket_failed(str(e))
                logging.exception(e)
                sys.exit(-1)
        else:
            logging.error('unknown ticket type')
            w.c3tt.set_ticket_failed('unknown ticket type')
            sys.exit(-1)
    else:
        sys.exit(0)

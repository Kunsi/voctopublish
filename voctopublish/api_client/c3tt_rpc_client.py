#    Copyright (C) 2016 derpeter
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

import xmlrpc.client
import hashlib
import hmac
import socket
import urllib
import xml
import logging


class C3TTClient:
    """
    group: worker group
    secret: client secret
    host: client hostname (will be taken from local host if set to None)
    url: tracker url (without the rpc)
    """

    def __init__(self, url, group, host, secret):
        self.url = url + "rpc"
        self.group = group
        self.host = host
        self.secret = secret
        self.ticket_id = None

    def _gen_signature(self, method, args):
        """
        generate signature
        assemble static part of signature arguments
        1. URL  2. method name  3. worker group token  4. hostname
        :param method:
        :param args:
        :return:
        """
        sig_args = urllib.parse.quote(self.url + "&" + method + "&" + self.group + "&" + self.host + "&", "~")

        # add method args
        if len(args) > 0:
            i = 0
            while i < len(args):
                arg = args[i]
                if isinstance(arg, bytes):
                    arg = arg.decode()
                if isinstance(arg, dict):
                    kvs = []
                    for k, v in args[i].items():
                        kvs.append(urllib.parse.quote('[' + str(k) + ']', '~') + '=' + urllib.parse.quote(str(v), '~'))
                    arg = '&'.join(kvs)
                else:
                    arg = urllib.parse.quote(str(arg), '~')

                sig_args = str(sig_args) + str(arg)
                if i < (len(args) - 1):
                    sig_args = sig_args + urllib.parse.quote('&')
                i += 1

        # generate the hmac hash with the key
        hash_ = hmac.new(bytes(self.secret, 'utf-8'), bytes(sig_args, 'utf-8'), hashlib.sha256)
        return hash_.hexdigest()

    def _open_rpc(self, method, args=[]):
        """
        create xmlrpc client
        :param method:
        :param args:
        :return:
        """
        logging.debug('creating XML RPC proxy: ' + self.url + "?group=" + self.group + "&hostname=" + self.host)
        if self.ticket_id:
            args.insert(0, self.ticket_id)

        try:
            proxy = xmlrpc.client.ServerProxy(self.url + "?group=" + self.group + "&hostname=" + self.host)
        except xmlrpc.client.Fault as err:
            msg = "A fault occurred\n"
            msg += "Fault code: %d \n" % err.faultCode
            msg += "Fault string: %s" % err.faultString
            raise C3TTException(msg) from err

        except xmlrpc.client.ProtocolError as err:
            msg = "A protocol error occurred\n"
            msg += "URL: %s \n" % err.url
            msg += "HTTP/HTTPS headers: %s\n" % err.headers
            msg += "Error code: %d\n" % err.errcode
            msg += "Error message: %s" % err.errmsg
            raise C3TTException(msg) from err

        except socket.gaierror as err:
            msg = "A socket error occurred\n"
            msg += err
            raise C3TTException(msg) from err

        args.append(self._gen_signature(method, args))

        try:
            logging.debug(method + str(args))
            result = getattr(proxy, method)(*args)
        except xml.parsers.expat.ExpatError as err:
            msg = "A expat err occured\n"
            msg += err
            raise C3TTException(msg) from err
        except xmlrpc.client.Fault as err:
            msg = "A fault occurred\n"
            msg += "Fault code: %d\n" % err.faultCode
            msg += "Fault string: %s" % err.faultString
            raise C3TTException(msg) from err
        except xmlrpc.client.ProtocolError as err:
            msg = "A protocol error occurred\n"
            msg += "URL: %s\n" % err.url
            msg += "HTTP/HTTPS headers: %s\n" % err.headers
            msg += "Error code: %d\n" % err.errcode
            msg += "Error message: %s" % err.errmsg
            raise C3TTException(msg) from err
        except OSError as err:
            msg = "A OS error occurred\n"
            msg += "Error message: %s" % err
            raise C3TTException(msg) from err

        return result

    def get_version(self):
        """
        get Tracker Version
        :return:
        """
        return str(self._open_rpc("C3TT.getVersion"))

    def assign_next_unassigned_for_state(self, ticket_type, to_state):
        """
        check for new ticket on tracker and get assignment
        :param ticket_type: type of ticket
        :param to_state: ticket state the returned ticket will be in after this call
        :return:
        """
        ret = self._open_rpc("C3TT.assignNextUnassignedForState", [ticket_type, to_state])
        # if we get no xml here there is no ticket for this job
        if not ret:
            return False
        else:
            self.ticket_id = ret['id']
            return ret['id']

    def set_ticket_properties(self, properties):
        """
        set ticket properties
        :param properties:
        :return:
        """
        ret = self._open_rpc("C3TT.setTicketProperties", [properties])
        if not ret:
            logging.error("no xml in answer")
            return False
        else:
            return True

    def get_ticket_properties(self):
        """
        get ticket properties
        :return:
        """
        ret = self._open_rpc("C3TT.getTicketProperties")
        if not ret:
            logging.error("no xml in answer")
            return None
        else:
            return ret

    def set_ticket_done(self):
        """
        set Ticket status on done
        :return:
        """
        ret = self._open_rpc("C3TT.setTicketDone")
        logging.debug(str(ret))

    def set_ticket_failed(self, error):
        """
        set ticket status on failed an supply a error text
        :param error:
        :return:
        """
        self._open_rpc("C3TT.setTicketFailed", [error.encode('ascii', 'xmlcharrefreplace')])


class C3TTException(Exception):
    pass

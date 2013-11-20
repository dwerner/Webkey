#!/usr/bin/python
import json
import socket
import struct
import sys
import time
from threading import Condition
from threading import Lock
from threading import Thread


class User(object):
    def __init__(self):
        self.random = ''
        self.connlist = []
        self.lock = Condition()

    def closeAllConnections(self):
        self.lock.acquire()
        while len(self.connlist):
            self.connlist.pop().close()
        self.lock.notifyAll()
        self.lock.release()

    def appendUserConnectionn(self, c):
        self.lock.acquire()
        try:
            #set keepalive on this connection
            c.setsockopt( socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            #the following socket options are commented out because they are not supported on MacOS/Win32

            #c.setsockopt( socket.SOL_TCP, socket.TCP_KEEPCNT, 6)
            #c.setsockopt( socket.SOL_TCP, socket.TCP_KEEPIDLE, 180)
            #c.setsockopt( socket.SOL_TCP, socket.TCP_KEEPINTVL, 10)
        except Exception as e:
            print "error in setting up keepalive ", e
        self.connlist.append(c)
        if len(self.connlist) > 3:
            self.connlist.pop(0).close()
        self.lock.notifyAll()
        self.lock.release()

    def getUserConnection(self):
        exptime = time.time()+10
        self.lock.acquire()
        while len(self.connlist) == 0:
            try:
                self.lock.wait(10)
            except RuntimeError:
                self.lock.release()
                return None
            if exptime < time.time():
                break
        if len(self.connlist) == 0:
            print "Got signal, but no connection"
            self.lock.release()
            return None
        c = self.connlist.pop(0)
        self.lock.release()
        return c

class UserList(object):
    def __init__(self):
        self.users = {}
        self.connlist = [] # this list will contains the active connections from the phone
        self.lock = Condition() # connlist is protected by this lock

    def get(self, name):
        try:
            return self.users[name]
        except:
            pass
        return None

    def add(self, name, random):
        print "Adding new user: ", name, " random: ", random
        u = User()
        u.random = random
        self.users[name] = u
        Persistence.save()

    def closeAllConnections(self):
        for name, user in self.users.iteritems():
            user.closeAllConnections()

# serialize "users" with username, random id
class Persistence(object):
    lock = Lock()

    @classmethod
    def save(cls):
        cls.lock.acquire(True)
        try:
            dict_users_random = {}
            for name, user in users.users.iteritems():
                dict_users_random[name] = user.random
            serialized = json.dumps(dict_users_random)
            with open("users", "w") as users_file:
                users_file.write(serialized)
        except Exception as e:
            print "Error saving users"
        cls.lock.release()

    @classmethod
    def load(cls):
        try:
            with open("users", "r") as users_file:
                unserialized = json.load(users_file)
                for name, random in unserialized.iteritems():
                    users.add(name, random)
        except Exception as e:
            print "Error loading users"


class ConnectionThread(Thread):
    def __init__(self,c):
        Thread.__init__(self)
        self.conn = c

    # handle a connection
    def run(self):
        
        #Get the first line of request from the socket, so we can identify the type of request
        try:
            firstline = self.conn.recv(4096)
        except:
            self.conn.close()
            return
        
        #Legacy?
        firstline = firstline.replace("/remote/", "/")

        #Phone requests
        if firstline.startswith("GET /register_") or firstline.startswith("WEBKEY"):
            self.phoneclient(firstline)

        # Browser requests
        else:
            self.browserclient(firstline)
      
    # wrap connection to the browser in a try-except, returning false where an exception is thrown.
    def trySendAll(self,data):
        try:
            self.conn.sendall(data)
        except:
            return False
        return True

    # handle what we consider to be PHONE connections (device/user connections)
    def phoneclient(self,firstline):
        print "PHONE ", firstline

        #find the position of the first / in "GET /username ..." or "WEBKEY username/random"
        p = firstline.find("/")
        if p == -1: conn.close(); return

        # provide a /register_username route. 
        if firstline.startswith("GET /register_"):
            p = firstline[14:].find("/")+14
            q = firstline[p+1:].find("/")+p+1
            e = firstline[q+1:].find(" ") + q+1

            username = firstline[14:p]
            random = firstline[p+1:q]
            print "register, username = "+username+", random = "+random+ " " ;

            if users.get(username):
                self.trySendAll("HTTP/1.1 200 OK\r\n\r\nUsername is already used.")
            else:
                users.add(username, random)
                self.trySendAll("HTTP/1.1 200 OK\r\n\r\nOK")
            self.conn.close()

        #otherwise, we have a "WEBKEY username/random" type of request. This is extending HTTP... :S
        else:

          #parse out fields from the request : username, random, port
          username = firstline[7:p] # as in "WEBKEY "...
          q = firstline[p+1:].find("/")+p+1
          g = firstline[q+1:].find("/")+q+1
          e = firstline[g+1:].find("\r") + g+1

          random = firstline[p+1:q]
          port = firstline[g+1:e]

          print "username from phone =",username

          #Important - store the socket connections in a UserList for later use.
          if users.get(username) and users.get(username).random == random:
              users.get(username).appendUserConnectionn(self.conn)
          else:
              #But... if we don't have the correct username or "random" then we close the connection. 
              print "Wrong username or random"
              self.trySendAll("stop")
              self.conn.close();

    # Handle requests determined to be from the client browser
    def browserclient(self,firstline):
        print "BROWSER ", firstline[:firstline.find("\r\n")]
        u = None

        #Find the request verb. i.e. GET
        req = firstline[:firstline.find(' ')]

        print "REQ is", req, len(req)

        # Find the position of the / AFTER "GET /"
        p = firstline[len(req)+2:].find("/")

        #Also find the first space AFTER "GET /"
        s = firstline[len(req)+2:].find(" ")
        
        #determining the location of the second /, and the space lets us
        # determine if this is a "GET /username/sotherOtherThing/ ..." (Type A)
        # or a "GET /username ..." (Type B) type of request 
        if s < p:
            p = s
        print "P is ", p

        #Always fish out the username from "GET /username..."
        username = firstline[len(req) + 2:p+len(req)+2]
        print "Username:", username

        #Check for an active connection to that username:
        u = users.get(username)
        if u == None:
            self.trySendAll("HTTP/1.1 404 Not Found\r\n\r\nPhone is unknown\r\n")
            self.conn.close()
            return

        #Rewrite the request as "GET /username..." => "GET /..."
        data = req + " " + firstline[p+len(req)+2:]
        print "REWRITTEN as", data[:data.find("\r\n")]
        data2 = ""
        while 1:
            e = data.find("\r\n\r\n")
            if e != -1:

                #Chop the request into two parts:
                # Part 2 (data2): everything after the first occurance of two consecutive newlines
                # Part 1 (data): everything up to the first two consecutive newlines \r\n\r\n
                data2 = data[e+4:]
                data = data[:e+4]

                #break the loop!
                break

            #later check the length of data to validate a socket read
            dl = len(data)
            try:
                #if we didnt find two consecutive newlines in the above loop
                # then we might need to recieve more data from the socket
                # buffer 4096 bytes into -data
                data += self.conn.recv(4096)
            except:
                self.conn.close()
                return
            # if we couldnt read up to another \r\n\r\n, then we want to close the probably bad connection
            if dl == len(data):
                #print data
                print "no endline, exiting"
                self.conn.close()
                return;

        #if there is a Content-Length header in the data, we can use that to buffer the correct amount 
        p = data.find("Content-Length:")
        if p != -1:
          
            #magic? magic numbers.... yeesh
            i = p+16

            # ...finding the location of the end of a range of digits
            while i < len(data) and '0' <= data[i] <= '9':
                i+=1;

            #clamp the count of the start (magic number) to the end (i) with min to 33554432 (32*1024*1024)
            cl = int(data[p+16:i])
            cl = min(cl,32*1024*1024) #There is a limit
            while 1:

                #again storing the size of our existing response
                dl = len(data2)
                
                if len(data2) >= cl:
                    break
                try:
                    #buffer into data2 with 4096 byte chunks
                    data2 += self.conn.recv(4096)
                except:
                    return

                #if we didn't get anything, then the connection's probably bad
                if dl == len(data2):
                    self.conn.close()
                    return

        #add all the data retrieved from the response back into one place
        data += data2

        #but if we didn't find any data, then close the connection, again
        if not data:
            self.conn.close()
            return

        # send all data to the user connection
        c=None
        while 1:
            c = u.getUserConnection()
            if c == None:
                self.trySendAll("HTTP/1.1 404 Not Found\r\n\r\nPhone is not online\r\n")
                self.conn.close()
                return
            try:
                c.sendall(data)
                break
            except:
                print "sendall error"
                c.close()

        # stream all data through the other connection (browser) 
        s = 0
        while 1:
            data = None
            try:
                data = c.recv(4096)
                s += len(data)
                if not data: break
                self.trySendAll(data)
            except:
                break

        #finally clean up if we reach here

        #close the browser connection
        self.conn.close()

        #close the device (user) connection
        c.close()


# Main entry point of Script 
users = UserList()
Persistence.load()

HOST = ''
PORT = 9934
if len(sys.argv) > 1:
    try:
        PORT = int(sys.argv[1]) # optionally pass PORT to this script from the command line
    except:
        pass

#Bind a socket to listen to HOST and PORT above
clientsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
clientsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

clientsock.bind((HOST, PORT)) # should we also be 'listen'ing as below?

clientsock.listen(9500) #unknown if this is used.

print "server started"

while 1:
    try:
        conn, addr = clientsock.accept()
        print 'Connected by', addr
        ConnectionThread(conn).start()

    except KeyboardInterrupt:
        try:
            clientsock.close() 
        except:
            pass
        break
    except socket.error:
        pass

print "stopping"

clientsock.shutdown(socket.SHUT_RDWR)
clientsock.close()

users.closeAllConnections()

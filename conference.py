#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'


from datetime import datetime

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import ndb

from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import StringMessage
from models import BooleanMessage
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import TeeShirtSize

from settings import WEB_CLIENT_ID
from settings import ANDROID_CLIENT_ID
from settings import IOS_CLIENT_ID
from settings import ANDROID_AUDIENCE

from utils import getUserId
from models import SessionForm
from models import SessionForms
from models import Session
from models import SessionWishlist

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
ANNOUNCEMENT_TPL = ('Last chance to attend! The following conferences '
                    'are nearly sold out: %s')
MEMCACHE_FEATUREDSPKR_KEY = "FEATURED_SPEAKER"
SPKR_TPL = ('These sessions will have our featured speaker %s: %s, %s')
SPEAKER = " "

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": [ "Default", "Topic" ],
}

OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
            }

FIELDS =    {
            'CITY': 'city',
            'TOPIC': 'topics',
            'MONTH': 'month',
            'MAX_ATTENDEES': 'maxAttendees',
            }

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)

SESSION_POST_REQUEST = endpoints.ResourceContainer(
    SessionForm,
    websafeConferenceKey=messages.StringField(1),
)

SESSION_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

SESSIONTYPE_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
    sessionType=messages.StringField(2),
)

SESSIONNAME_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    name=messages.StringField(1),
    )

SESSIONSDATE = endpoints.ResourceContainer(
    message_types.VoidMessage,
    sdate=messages.StringField(1),
    )

SESSIONSPKR_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    speaker=messages.StringField(1),
    )

WISHLIST_POST_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    SessionKey=messages.StringField(1),
    )
# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -


@endpoints.api(name='conference', version='v1', audiences=[ANDROID_AUDIENCE],
    allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID, ANDROID_CLIENT_ID, IOS_CLIENT_ID],
    scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf


    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={'email': user.email(),
            'conferenceInfo': repr(request)},
            url='/tasks/send_confirmation_email'
        )
        return request


    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
            http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)


    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)


    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='getConferencesCreated',
            http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
        )


    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q


    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)


    @endpoints.method(ConferenceQueryForms, ConferenceForms,
            path='queryConferences',
            http_method='POST',
            name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
                items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in \
                conferences]
        )


# - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf


    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key = p_key,
                displayName = user.nickname(),
                mainEmail= user.email(),
                teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile      # return Profile


    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        #if field == 'teeShirtSize':
                        #    setattr(prof, field, str(val).upper())
                        #else:
                        #    setattr(prof, field, val)
                        prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)


    @endpoints.method(message_types.VoidMessage, ProfileForm,
            path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()


    @endpoints.method(ProfileMiniForm, ProfileForm,
            path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)


# - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = ANNOUNCEMENT_TPL % (
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement


    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='conference/announcement/get',
            http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        return StringMessage(data=memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY) or "")


# - - - Featured Speaker - - - - - - - - - - - - - - - - - - - -
#TASK 4
    @staticmethod
    def _setCacheFeaturedSpkr(fspkr):
        """Set the Featured Speaker cache announcement
        This is called by the SetFeaturedSpeaker taskqueue handler from main.py

        """
        q = Session.query()
        featured_sessions = q.filter(Session.speaker == fspkr).fetch()
        featured_speaker = fspkr
        # If the speaker matches criteria, add the details to the memcache
        # The new query doesnt seem to inculde the latest entity
        # Resorted to useing data['name'] as a workaround
        speaker_announcement = SPKR_TPL % (featured_speaker,
                ','.join(sess.name for sess in featured_sessions), fspkr)
        memcache.set(MEMCACHE_FEATUREDSPKR_KEY, speaker_announcement)
        return speaker_announcement


    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='sessions/featuredspeaker/get',
            http_method='GET', name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """Return Featured Sessions and speakers from memcache."""
        #TASK 4
        return StringMessage(data=memcache.get(MEMCACHE_FEATUREDSPKR_KEY) or "")



# - - - Featured Speaker - - - - - - - - - - - - - - - - - - - -
# - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='conferences/attending',
            http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser() # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)


        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf, names[conf.organizerUserId])\
         for conf in conferences]
        )


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='filterPlayground',
            http_method='GET', name='filterPlayground')
    def filterPlayground(self, request):
        """Filter Playground"""
        q = Conference.query()
        # field = "city"
        # operator = "="
        # value = "London"
        # f = ndb.query.FilterNode(field, operator, value)
        # q = q.filter(f)
        q = q.filter(Conference.city=="London")
        q = q.filter(Conference.topics=="Medical Innovations")
        q = q.filter(Conference.month==6)

        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, "") for conf in q]
        )
    
    def _copySessionToForm(self, session):
        """Copy relevant fields from Session to SessionForm"""
        sf = SessionForm()
        for field in sf.all_fields():
            if hasattr(session, field.name):
                setattr(sf, field.name, str(getattr(session, field.name)))
            #Generate a Session, urlsafe key, in order to query sessions etc
            elif field.name == 'urlsafeKey':
                setattr(sf, field.name, session.key.urlsafe())
        sf.check_initialized()
        return sf


    def _createSessionObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)
        #Use the websafekey to locate the associated conference
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                    'No conference found with key: %s' % wsck)
        #Validate that the logged in user is also the organizer
        if user_id != conf.organizerUserId:
            raise endpoints.UnauthorizedException(
                    'Only the organizer of this event may create a new session')
        #At this point, all checks are complete. Proceed with gathering 
        #data from SessionForm and put it in the Session DS

        #Generate a Session Key. Session is a child of conference
        #Use the conf key to form that relationship
        c_key = conf.key
        session_id = Session.allocate_ids(size=1, parent=c_key)[0]
        session_key = ndb.Key(Session, session_id, parent=c_key)
        #Now, copy data from the Sessionform to the Session DS
        if not request.name:
            raise endpoints.BadRequestException("Session 'name' field required")
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        #Remove the websafekey, since the datastore does not need it
        del data['websafeConferenceKey'] 
        del data['urlsafeKey'] 
        #Add the key to the collected data
        data['key'] = session_key
        #Convert the date from string to Date object
        if data['date']:
            ddate = datetime.strptime(data['date'][:10], "%Y-%m-%d").date()
            data['date'] = datetime.strptime(data['date'][:10], "%Y-%m-%d").date()
        #If the Session start date is before the conference start date raise an exception
        if (data['date'] < conf.startDate or data['date'] > conf.endDate):
                raise endpoints.BadRequestException("Session can only exist between conference start and end dates")
        #Convert the time from string to Time object
        if data['startTime']:
            stime = datetime.strptime(data['startTime'][:8], "%H:%M:%S").time()
            #ddate = datetime.strptime(data['date'][:10], "%Y-%m-%d").date()
            data['startTime'] = stime
        #Create the Session
        session = Session(**data).put()
        #TASK4
        #Check if the speaker already exists
        q = Session.query()
        if (q.filter(Session.speaker == data['speaker']).count() >= 1 ):
            #Off load setting memcache to a taskqueue. Send the speaker
            #name to the task. This will then be used by _setCacheFeaturedSpkr
            taskqueue.add( params={'featured_spkr':data['speaker']},
                    url = '/tasks/set_featured_speaker'
                    )

        return self._copySessionToForm(session.get())

    @endpoints.method(SESSION_POST_REQUEST, SessionForm, path='session',
            http_method='POST', name='createSession')
    def createSession(self, request):
        """Create a new Session"""
        #TASK 1
        return self._createSessionObject(request)

    @endpoints.method(SESSION_GET_REQUEST, SessionForms, path='getConferenceSessions',
            http_method='POST', name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """Return sessions associated with a conference"""
        #TASK 1
        conf_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        #Error handling
        if not conf_key:
            raise endpoints.NotFoundException(
                    'No conference found with key: %s' % request.websafeConferenceKey)
                
        sessions = Session.query(ancestor=conf_key).fetch()
        #Error Handling
        if not sessions:
            raise endpoints.NotFoundException(
                    'No sessions found with conference key: %s' % request.websafeConferenceKey)
        return SessionForms(
                items=[self._copySessionToForm(session) for session in sessions])

    @endpoints.method(SESSIONTYPE_GET_REQUEST, SessionForms, path='getConferenceSessionsByType',
            http_method='POST', name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """Return sessions of a particular type, associated with a conference"""
        #TASK 1
        conf_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        #Error Handling
        if not conf_key:
            raise endpoints.NotFoundException(
                    'No conference found with key: %s' % request.websafeConferenceKey)
        sessions = Session.query(ancestor=conf_key)
        #Error Handling
        if not sessions:
            raise endpoints.NotFoundException(
                    'No sessions found with conference key: %s' % request.websafeConferenceKey)
        sessionsType = sessions.filter(Session.typeOfSession == request.sessionType).fetch()
        return SessionForms(
                items=[self._copySessionToForm(session) for session in sessionsType])


    @endpoints.method(SESSIONSPKR_GET_REQUEST, SessionForms, path='getConferenceSessionsBySpeaker',
            http_method='POST', name='getConferenceSessionsBySpeaker')
    def getConferenceSessionsBySpeaker(self, request):
        """Return sessions by a speaker across all conference"""
        #TASK 1
        sessions = Session.query()
        #Error Handling
        if not sessions:
            raise endpoints.NotFoundException(
                    'No sessions found. Please create sessions first.')
        sessionsSpkr = sessions.filter(Session.speaker == request.speaker).fetch()
        return SessionForms(
                items=[self._copySessionToForm(session) for session in sessionsSpkr])
    
    @endpoints.method(SESSIONNAME_GET_REQUEST, SessionForms, path='getConferenceSessionsByName',
            http_method='POST', name='getConferenceSessionsByName')
    def getConferenceSessionsByName(self, request):
        """Return sessions that match a name, across conferences"""
        #TASK 3, Addnl Query 1
        sessions = Session.query()
        #Error Handling
        if not sessions:
            raise endpoints.NotFoundException(
                    'No sessions found. Please create sessions first.')
        sessionsName = sessions.filter(Session.name == request.name).fetch()
        return SessionForms(
                items=[self._copySessionToForm(session) for session in sessionsName])

    @endpoints.method(SESSIONSDATE, SessionForms, path='getConferenceSessionsBySDate',
            http_method='POST', name='getConferenceSessionsBySDate')
    def getConferenceSessionsBySDate(self, request):
        """Return sessions that begin on or after a requested start-date, across conferences"""
        #TASK 3 Addnl Query 2
        #Convert the requested start date from string to date format
        req_sdate = datetime.strptime(request.sdate[:10],"%Y-%m-%d").date() 
        sessions = Session.query()
        #Error Handling
        if not sessions:
            raise endpoints.NotFoundException(
                    'No sessions found. Please create sessions first.')
        sessionsDate = sessions.filter(Session.date >= req_sdate).fetch()
        return SessionForms(
                items=[self._copySessionToForm(session) for session in sessionsDate])

    @endpoints.method(WISHLIST_POST_REQUEST, SessionForm, path='sessionWishlist',
            http_method='POST', name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        #TASK 2
        """Adds sessions to a user wishlist & returns the sessions added"""
        #Check if the user is logged in
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization Required')
        user_id = getUserId(user)
        #Validate that the SessionKey (urlsafe key) is provided
        if not request.SessionKey:
            raise endpoints.BadRequestException("SessionKey field required") 
        #Validate whether the requested SessionKey is already in the user's wishlist
        q = SessionWishlist.query()
        if (q.filter(SessionWishlist.sessionKey == request.SessionKey).count() > 0):
                raise endpoints.BadRequestException("SessionKey is already in %s's wishlist" % user)
        #Generate a Wishlist key to store the user wishlist. The wishlist will be created as
        #a child of Profile
        p_key = ndb.Key(Profile, user_id)
        w_id = SessionWishlist.allocate_ids(size=1, parent=p_key)[0]
        w_key = ndb.Key(SessionWishlist, w_id, parent=p_key)
        #Add the wishlist to the DS
        wishlist = SessionWishlist( key = w_key, sessionKey = request.SessionKey)
        wl_key = wishlist.put()
        #Return the session associated with the created entry
        session = ndb.Key(urlsafe=request.SessionKey).get()
        return self._copySessionToForm(session)

    @endpoints.method(message_types.VoidMessage, SessionForms, path='getsessionsWishlist',
            http_method='GET', name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):
        """Get user wishlist sessions"""
        #TASK 2
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization Required')
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        #Query the list of wishlist sessions based on ancestor key p_key
        wishlist = SessionWishlist.query(ancestor=p_key).fetch()
        #generate a list of sessions using the sessionKey stored in the wishlist
        sessions = []
        for wish_session in wishlist:
            sessions.append(ndb.Key(urlsafe=wish_session.sessionKey).get())

        return SessionForms(
                items=[self._copySessionToForm(session) for session in sessions]
                )
        
    @endpoints.method(message_types.VoidMessage, SessionForms,
            path='sessionsfilter',
            http_method='GET', name='sessionsMultipleInequalitiesFilter')
    def sessionsMultipleInequalitiesFilter(self, request):
        """Multiple property multiple inequalities Filter Playground"""
        #TASK 3 Session filter 
        #Filter sessions that are not workshops and that start on or after 1900 hrs
        # Datetime reference time for comparing with stored value
        too_late = datetime.strptime("19:00:00", "%H:%M:%S").time()
        # collect all sessions based on one inequality filter for one of the properties
        q1 = Session.query()
        sessions = q1.filter(Session.startTime < too_late).fetch()
        #Since multiple inequalites filters across different properties are not allowed
        #iterate through the filtered properties and use python to selectively remove 
        #undesired properties
        for sess in sessions:
            if sess.typeOfSession == "Workshop":
                sessions.remove(sess)

 
        return SessionForms(
                items=[self._copySessionToForm(session) for session in sessions]
                )


 


api = endpoints.api_server([ConferenceApi]) # register API

App Engine application for the Udacity training course.

## Products
- [App Engine][1]

## Language
- [Python][2]

## APIs
- [Google Cloud Endpoints][3]

## Setup Instructions
1. Update the value of `application` in `app.yaml` to the app ID you
   have registered in the App Engine admin console and would like to use to host
   your instance of this sample.
1. Update the values at the top of `settings.py` to
   reflect the respective client IDs you have registered in the
   [Developer Console][4].
1. Update the value of CLIENT_ID in `static/js/app.js` to the Web client ID
1. (Optional) Mark the configuration files as unchanged as follows:
   `$ git update-index --assume-unchanged app.yaml settings.py static/js/app.js`
1. Run the app with the devserver using `dev_appserver.py DIR`, and ensure it's running by visiting your local server's address (by default [localhost:8080][5].)
1. (Optional) Generate your client library(ies) with [the endpoints tool][6].
1. Deploy your application.


[1]: https://developers.google.com/appengine
[2]: http://python.org
[3]: https://developers.google.com/appengine/docs/python/endpoints/
[4]: https://console.developers.google.com/
[5]: https://localhost:8080/
[6]: https://developers.google.com/appengine/docs/python/endpoints/endpoints_tool

##Session related endpoints:
To access the session endpoints, after deploying the app following the above instructions, navigate to localhost:<LISTNER_PORT>/_ah/api/explorer. This can also be tested through the public
internet by registering and deploying the app, followed by <app-id>.appspot.com/_ah/api/explorer. 
The following endpoints were developed to address the different tasks for the course:
###TASK 1:
createSession: This method invokes a _createSessionObject method that collects data from the request and copies it to the datastore
getConferenceSessions: This method returns all sessions associated with a given conference (referenced through it's urlsafe key
getConferenceSessionsByType: This method returns all sessions across all conferences based on the session type
getSessionsBySpeaker: This method returns all sessions across all conferences, queried by username
####Class Implementations:
The session was created as a child of Conference. This helped neatly tie the inheritance and helped query by the ancestor dependancy.
The speaker was implemented as a simple field for the existing Session Entity. For the functional requirements of the project, it appeared reasonable not to separate this out into a separte entity and complicate the endpoint tie up (requirement for a parent/child relationship between Session and Speaker). This follows the KISS principle.
_Updates based on code review_
The SessionForm class was implemented with all static variables as a StringField. This was largely based on the fact that strings are more generic and can handle most of the input/output.
The Session class had the name of the session mandatory. All the entity properties were stored as strings except for 'date' and 'startTime'. This allowed for using conditional validation/comparison 
(with >, < & ==) of the stored data and user supplied or other criteria
###TASK 2:
addSessionToWishlist: This method adds the desired session to the currently logged in users' wishlist. 
getSessionsInWishlist: Simply returns all the sessions in the users' wishlist
####Class Implementation:
A single class called SessionWishlist holds the sessionkey of the session as the users' wishlist. The entities are created with a parent-child relation with the users' profile.
###TASK 3:
getConferenceSessionsBySDate: This method helps users identify sessions with a start date earlier than a date they specify. This will help provide the user with an option to get a list of sessions based on their prefered start-time
getConferenceSessionsByName: This method provides the user a quick way to locate the session details for a session whose name is already known.
####Solution to the multiple property, multiple inequality problem
sessionsMultipleInequalitiesFilter: This method explores the possibility of how to handle multiple inequalities. In this implementation, the method executes the filter query for one equality (sessions before 1900hrs). It then relies on python to iterate through the result set to remove sessions that needs to be filtered out based on the second inequality check (filter out workshops)
###TASK 4:
Everytime a new session is created in the _createSessionObject method, a query is executed right after to validate whether the speaker for the session is presenting at more than one session.
In that case, the speaker was added to memcache as a featured speaker and the sessions he/she was presenting was also presented. This can be used as an inexpensive way to highlight sessions
and the speaker.
getFeaturedSpeaker: This is an endpoint implementation to retrive data from the memcache for the featured speaker

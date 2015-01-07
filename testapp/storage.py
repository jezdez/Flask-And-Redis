"""
===============
testapp.storage
===============

Implement working with Redis storages.

"""

import time

from collections import OrderedDict

from flask import current_app

from compat import iteritems, iterkeys
from constants import (
    COMMENT_KEY,
    THREAD_KEY,
    THREAD_COMMENTS_KEY,
    THREAD_COUNTER_KEY,
    THREADS_KEY,
)
from utils import uid


content = current_app.extensions['redis']['REDIS_CONTENT']
links = current_app.extensions['redis']['REDIS_LINKS']
prefix = current_app.config['KEY_PREFIX']


def add_comment(thread_uid, author, text):
    """Add comment to specefied thread.

    :param thread_uid: Thread unique ID.
    :param author: Author of comment.
    :param text: Comment text.
    """
    # Setup new UID for comment
    comment_uid = uid()

    # Store comment metadata and rewrite data about last comment for thread
    with content.pipeline() as pipe:
        pipe.hmset(COMMENT_KEY.format(prefix, thread_uid, comment_uid), {
            'author': author,
            'text': text,
            'timestamp': time.time(),
        })
        pipe.hmset(THREAD_KEY.format(prefix, thread_uid), {
            'last_comment_uid': comment_uid,
        })
        pipe.execute()

    # Put comment to comments list and incr comments counter for thread
    with links.pipeline() as pipe:
        pipe.rpush(THREAD_COMMENTS_KEY.format(prefix, thread_uid), comment_uid)
        pipe.incr(THREAD_COUNTER_KEY.format(prefix, thread_uid))
        pipe.execute()

    # Comment added, everything is alright
    return True


def delete_thread(thread_uid):
    """Delete thread and all its data from storages.

    :param thread_uid: Thread unique ID.
    """
    pipe = content.pipeline()
    pipe.delete(THREAD_KEY.format(prefix, thread_uid))

    comments_key = THREAD_COMMENTS_KEY.format(prefix, thread_uid)
    for comment_uid in links.lrange(comments_key, 0, -1):
        pipe.delete(COMMENT_KEY.format(prefix, thread_uid, comment_uid))

    pipe.execute()

    with links.pipeline() as pipe:
        pipe.delete(comments_key)
        pipe.delete(THREAD_COUNTER_KEY.format(prefix, thread_uid))
        pipe.lrem(THREADS_KEY.format(prefix), 1, thread_uid)
        pipe.execute()

    return True


def get_thread(thread_uid, last_comment=False, counter=False):
    """Read thread metadata by specefied UID.

    :param thread_uid: Thread unique ID.
    :param last_comment: Include last comment metadata or not?
    :param counter: Include comments counter or not?
    """
    thread = content.hgetall(THREAD_KEY.format(prefix, thread_uid))
    if not thread:
        return thread

    last_comment_uid = thread.get('last_comment_uid')
    if last_comment and last_comment_uid:
        comment_key = COMMENT_KEY.format(prefix, thread_uid, last_comment_uid)
        thread['last_comment'] = content.hgetall(comment_key)

    if counter:
        counter_key = THREAD_COUNTER_KEY.format(prefix, thread_uid)
        thread['comments_counter'] = links.get(counter_key)

    return thread


def list_comments(thread_uid):
    """List all comments for given thread.

    :param thread_uid: Thread unique UID.
    """
    with content.pipeline() as pipe:
        comments_key = THREAD_COMMENTS_KEY.format(prefix, thread_uid)
        uids = []
        for comment_uid in links.lrange(comments_key, 0, -1):
            pipe.hgetall(COMMENT_KEY.format(prefix, thread_uid, comment_uid))
            uids.append(comment_uid)
        return OrderedDict(zip(uids, pipe.execute()))


def list_threads():
    """List all available threads in most efficient way."""
    # Read Threads from Links and Content databases
    with content.pipeline() as pipe:
        uids = []
        for thread_uid in links.lrange(THREADS_KEY.format(prefix), 0, -1):
            pipe.hgetall(THREAD_KEY.format(prefix, thread_uid))
            uids.append(thread_uid)
        threads = OrderedDict(zip(uids, pipe.execute()))

    # Make another multi request for threads' counters and last comments where
    # possible
    comments_request = OrderedDict()

    for thread_uid, thread in iteritems(threads):
        last_comment_uid = thread.get('last_comment_uid')
        if not last_comment_uid:
            continue
        comments_request[thread_uid] = thread['last_comment_uid']

    # We assume that last comment and comments counter available only for
    # threads with comments
    if comments_request:
        with links.pipeline() as pipe:
            for thread_uid in iterkeys(comments_request):
                pipe.get(THREAD_COUNTER_KEY.format(prefix, thread_uid))
            response = zip(iterkeys(comments_request), pipe.execute())

        for thread_uid, counter in response:
            threads[thread_uid]['comments_counter'] = counter

        with content.pipeline() as pipe:
            for thread_uid, comment_uid in iteritems(comments_request):
                key = COMMENT_KEY.format(prefix, thread_uid, comment_uid)
                pipe.hgetall(key)
            response = zip(iterkeys(comments_request), pipe.execute())

        for thread_uid, comment in response:
            threads[thread_uid]['last_comment'] = comment

    return threads


def start_thread(author, subject, comment=None):
    """Start new thread with or without comment.

    :param author: Thread author.
    :param subject: Thread subject.
    :param comment: Text for first comment if any.
    """
    # Setup new UID for the Thread and add it to Links and Content storages
    thread_uid = uid()

    threads_key = THREADS_KEY.format(prefix)
    thread_key = THREAD_KEY.format(prefix, thread_uid)

    content.hmset(thread_key, {
        'author': author,
        'subject': subject,
        'timestamp': time.time(),
    })
    links.lpush(threads_key, thread_uid)

    # Add comment, but only if it not empty
    if comment:
        add_comment(thread_uid, author, comment)

    # Everything is OK
    return True

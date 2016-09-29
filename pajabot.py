#!/usr/bin/env python2
# -*- encoding: utf-8 -*-

import os
import select
import socket
import string
import subprocess
import time
import random
import re
import RPi.GPIO as GPIO

# Some variables for easy access
HOST = "palvain"
PORT = 6667
NICK = "nikki"
CHAN = "!kanava"

swear_words = [
    "voivoi",
]

annoyed_replies = [
    "samat kuin äskenkin",
    "...sulla joku kiire?",
    "rauhoitu",
    "eeehhh, just sanoin?",
    "nääh",
    "vastahan mä kerroin",
    "emmä koko ajan jaksa toistaa samaa",
    "ai pajalla? olisko nää: %s",
    "%s ainakin, lol emt",
]

swearing_replies = [
    "nyt hiljaa hemmetti",
]

greetings = [
    "i told you i would be back.",
    "miksi ;__;",
    "NONIIN!",
    "zeeky boogy doog",
    "Emil, miksi rikoit ;__;",
    "huoltotauko ohi",
    "rupean urheilemaan, perl jääköön sikseen",
]

PEOPLE_PRESENT_ANNOYED_TIMER = 60
people_present = set()
people_present_last_set = people_present
people_present_last_time = time.time()
people_present_regex = re.compile(r"(<.*?>\s+?)?((kes|kuka|ket(ä|äs|än)|ke(it|tk)ä(hän)?|joku|ehkä|varmaan(kin)?|onko?s?)\s+.*)?(paikal(la)?|(ele)?pajal(la)?|läsnä|pajautt(ama|ele)[msae]+)\s*?\?\s*$", flags=re.IGNORECASE)
swearwords_regex = re.compile(r".*(hemmetti).*", flags=re.IGNORECASE)
nick_and_rest_regex = re.compile(r"^{}[:,]\s*?(.*)".format(NICK), flags=re.IGNORECASE)


buf = ""
current_topic = None

GPIO.setmode(GPIO.BOARD)
GPIO.setup(22, GPIO.IN, pull_up_down=GPIO.PUD_UP)

laststate = GPIO.input(22)
lasttime = time.time()

state = laststate
lastreadstate = laststate
lastchangetime = lasttime

# Connect to server
sock = socket.socket()
sock.connect((HOST, PORT))
sock.setblocking(0)

doorlock_pipe = None
door_opener = None
door_timestamp = None
door_latch_timeout = 5 + 1

synth_process = None

# Register ourselves
sock.send("NICK %s\r\n" % NICK)
sock.send("USER %s ignore ignore :Pajatso\r\n" % NICK)
sock.send("JOIN :%s\r\n" % CHAN)
sock.send("PRIVMSG %s :%s\r\n" % (CHAN, random.choice(greetings)))


def re_replace_swearwords(match):
    return random.choice(swear_words)


def shellquote(s):
    return "'" + s.replace("'", "").replace("\"", "").replace("!", "."). \
        replace(";", ".").replace(":", ".").replace("\\", "").replace("(", ""). \
        replace(")", "") + "'"


def finnish_and(iterable):
    if len(iterable) == 0:
        return ""
    if len(iterable) == 1:
        return str(list(iterable)[0])
    wordlist = list(iterable)
    head = wordlist[:-1]
    last = wordlist[-1]
    combiner = random.choice([" ja ", " sekä "])
    return ", ".join(head) + combiner + last


def irc_notice(socket, target, message):
    sock.send("NOTICE %s :%s\r\n" % (target, message))


def irc_say(socket, target, message):
    sock.send("PRIVMSG %s :%s\r\n" % (target, message))


def irc_action(socket, target, message):
    sock.send("ACTION %s :%s\r\n" % (target, message))

while True:
    # Wait for timeout or data.
    if not doorlock_pipe:
        doorlock_pipe = os.fdopen(os.open("/tmp/ovi.pipe", os.O_RDONLY | os.O_NONBLOCK))
    try:
        ready, _, _ = select.select([sock, doorlock_pipe], [], [], 1)
    except KeyboardInterrupt:
        try:
            reason = raw_input("Enter quit reason or ^C again: ").strip()
        except KeyboardInterrupt:
            reason = "GOT SIGINT, BYE BYE!"
        sock.send("QUIT :%s\r\n" % reason)
        break

    # Save door opener info from pipe
    if doorlock_pipe in ready:
        door_opener = doorlock_pipe.read().strip()
        print people_present, "->",
        if state == 1:
            # Reading from the pipe failed
            if door_opener == "":
                irc_say(sock, CHAN, "joku saapui pajalle")
                irc_action(sock, CHAN, random.choice([
                    "Korjatkaa tää pliis",
                ]))

    # That person is already present
    if door_opener in people_present:
        irc_notice(sock, CHAN, '%s poistui pajalta' % door_opener)
        people_present.remove(door_opener)
        subprocess.call("aplay chimes.wav", shell=True)
        if len(people_present) == 0:
            irc_say(sock, CHAN, random.choice([
                'ikävä ;__;',
                'kaikki katosivat jonnekin ;__;',
                'älkää jättäkö yksin! ._.',
            ]))

    # That person is not present
    else:
        people_present.add(door_opener)
        subprocess.call("aplay tada.wav", shell=True)
        irc_notice(sock, CHAN, "%s saapui pajalle" % door_opener)

    print people_present
    door_timestamp = time.time()
    doorlock_pipe.close()
    doorlock_pipe = None

    # Handle socket messages
    if sock in ready:
        # Read from socket
        buf = buf + sock.recv(1000)
        linetab = string.split(buf, "\n")
        buf = linetab.pop()  # Put last partial line back to buffer

        # Process received lines
        for line in linetab:
            # print("received: %s" % line)
            line = line.lstrip(":").rstrip()
            before, _, after = line.partition(":")
            line = before.rstrip().split(" ")
            line.append(after)

            # Answer to PINGs
            if line[0] == "PING":
                sock.send("PONG %s\r\n" % line[1])

            elif (line[1] == "PRIVMSG") and (line[2] == CHAN) \
                and people_present_regex.match(line[3]):
                # Don't answer people who swear
                if swearwords_regex.match(line[3]):
                    irc_say(sock, CHAN, random.choice(swearing_replies))

                # Rate limit answers
                elif people_present_last_set == people_present \
                     and time.time() < people_present_last_time + PEOPLE_PRESENT_ANNOYED_TIMER:
                    # Somewhat of a hack. Allows us to keep both formatted and
                    # unformatted answers in one list.
                    # The only purpose of this while loop is to allow continuing to the beginning.
                    while True:
                        # Grab random unformatted string
                        unformatted = random.choice(annoyed_replies)
                        try:
                            people_list = finnish_and(people_present)
                            # Try to format string. This fails if it's an unformattable string.
                            formatted = unformatted % finnish_and(people_present)
                            # The real hack-inside-hack: if the string was formattable AND
                            # the list of people was empty, start from the beginning.
                            if people_list == "":
                                continue
                        except:
                            # We'll arrive here if the string is an unformattable one.
                            formatted = unformatted
                        break

                    irc_say(sock, CHAN, formatted)
                else:
                    if state == 0:
                        irc_say(sock, CHAN, random.choice([
                            "Paja on kiinni.",
                            "Paja on edelleenkin kiinni...",
                            "Paja on muuten kiinni",
                            "Ellet huomannut, niin paja on kiinni",
                            "Ei täällä ole ketään. Saa tulla pitämään mulle seuraa",
                            "Yksikseni täällä irkkailen",
                            "Täällä olen vain minä"
                        ]))
                    elif len(people_present) == 0:
                        irc_say(sock, CHAN, "Paja on auki, mutta kukaan ei ole paikalla?")
                        synth_process = subprocess.Popen("echo 'Unohditteko kirjautua ovikortilla "
                                                         "paikalle? Irkissä kysellään.' | iconv -f "
                                                         "UTF-8 -t ISO-8859-1 | festival --tts "
                                                         "--language finnish",
                                                         shell=True)
                    else:
                        prefix = random.choice([
                            "Elepajalla: ",
                            "Pajalla: ",
                            "Paikalla: ",
                        ])
                        irc_say(sock, CHAN, prefix + finnish_and(people_present))

                # Only update the timer if we've given a real answer
                people_present_last_set = people_present
                people_present_last_time = time.time()

            # Speech synth
            elif (line[1] == "PRIVMSG") and (line[2] == CHAN) and nick_and_rest_regex.match(line[3]):
                text = nick_and_rest_regex.match(line[3]).group(1)
                # festival can't always say z
                msgnick = string.split(line[0], "!")[0][1:].replace("z", "ts")

                # FIXME Reclaim. Is this needed?
                if synth_process is not None:
                    synth_process.terminate()
                    synth_process.wait()
                    synth_process = None

                subprocess.call("aplay pajabotin_aani.wav", shell=True)
                text = (text + " terveisin " + msgnick).replace("~", " tkk-")
                text = re.sub(r'([!\\\"#¤%&/(){}=\?+]{2,})', re_replace_swearwords, text)

                synth_process = subprocess.Popen("echo {} | iconv -f UTF-8 -t ISO-8859-1 | "
                                                 "festival --tts --language finnish".
                                                 format(shellquote(text)),
                                                 shell=True)

            # Invite
            elif line[1] == "INVITE" and line[3].lstrip(":") == CHAN:
                sock.send("JOIN %s\r\n" % CHAN)
                irc_say(sock, CHAN, random.choice(greetings))

    # Check door sensor state
    readstate = GPIO.input(22)
    curtime = time.time()

    if readstate != lastreadstate:
        lastchangetime = curtime
        lastreadstate = readstate

    timediff = curtime - lastchangetime
    # at least two successive reads should have the state "door open" before we do anything
    # (this should filter short RFI peaks and other glitches)
    message_extra = ""
    if readstate == 1 and state == 0 and timediff > 0:
        state = 1
        if door_opener is not None and door_timestamp + door_latch_timeout > time.time():
            message = "Paja on auki"
            message_extra = ", paikalla %s" % door_opener
            subprocess.call("aplay tada.wav", shell=True)
            if door_opener == "":
                irc_say(sock, CHAN, "Eräs: pliis korjatkaa tää")
            else:
                people_present.add(door_opener)  # Add door opener to set
        else:
            message = "Paja on auki"
            message_extra = ", avattu ilman kulkukorttia"

    # longer delay for state "door closed"
    elif readstate == 0 and timediff >= 20:
        state = 0
        message = "Paja on kiinni"
        people_present = set()  # Empty the door opener set

    if state != laststate:
        ovistatus = open('ovistatus.log', 'w')
        ovistatus.write(message + '\n')
        ovistatus.close()
        irc_notice(sock, CHAN, message + message_extra)
        laststate = state
        lasttime = time.time()

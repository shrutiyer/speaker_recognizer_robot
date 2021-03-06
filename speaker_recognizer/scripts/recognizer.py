#!/usr/bin/env python

import rospy

from python_speech_features import mfcc, delta, logfbank
import scipy.io.wavfile as wav
from scipy.cluster.vq import kmeans2, whiten, vq
import numpy as np
import glob

class HMM(object):

    def __init__(self, name=None, transitions=None, emissions=None, states=None):

        self.name = name
        self.transitions = transitions # Matrix of transision probabilities from one state to another. Size # of states by # of states
        self.emissions = emissions # Matrix of emission probabilities. Size # of states by # of observation symbol
        
        self.states = states # Includes start (q0) and end (qF) states. Along with N hidden states
        self.state_len = len(self.states)
        self.final_state_index = self.state_len-1

        # Gets set while training
        self.observations = None
        self.observation_len = 0
        self.final_observation_index = 0

        self.alpha = None
        self.beta = None
        self.zeta = None
        self.gamma = None

        self.gamma_sum = np.zeros(self.state_len)

    def emission_prob(self, state, time):
        return self.emissions[state][self.observations[time]-1]

    def get_observation_index(self, index):
        """Helper function to do indexing starting 1 instead"""
        return index-1

    def forward(self):
        """ Given an HMM, lambda = (transitions, emissions), and an observation sequence O, 
        determine the likelihood P(O|lambda) 
        observation: array of observations of length T"""

        # i: state_prime
        # j: state
        # t: time
        
        # Initialize alpha = Previous forward path probability. Size # of states by length of observation
        self.alpha = np.zeros((self.state_len, self.observation_len))

        # Initialization
        for state in range(1, self.final_state_index):
            self.alpha[state][self.get_observation_index(1)] = self.transitions[0][state]*self.emission_prob(state, self.get_observation_index(1))

        # Recursion
        for time in range(self.get_observation_index(2), self.observation_len): # Start from the second observation
            for state in range(1, self.final_state_index): # Exclude start and end states here
                for state_prime in range(1, self.final_state_index):
                    self.alpha[state][time] += self.alpha[state_prime][time-1]*self.transitions[state_prime][state]* \
                                    self.emission_prob(state, time) 

        # Termination
        for state in range(1, self.final_state_index):
            self.alpha[self.final_state_index][self.final_observation_index] += self.alpha[state][self.final_observation_index]* \
                                                                    self.transitions[state][self.final_state_index]
        # print "ALPHA"
        # print self.alpha
        return self.alpha[self.final_state_index][self.final_observation_index]

    def backward(self):
        """ Given an HMM, lambda, determine the probability, beta, of seeing the 
            observations from time t+1 to the end, given that we are in state i at time t."""

        # Initialize beta
        self.beta = np.zeros((self.state_len, self.observation_len))

        # Initialization
        for state in range(1, self.final_state_index):
            self.beta[state][self.final_observation_index] = self.transitions[state][self.final_state_index]
        
        # Recursion

        # i: state
        # j: state_prime
        # t: time
        for time in range(self.final_observation_index-1, self.get_observation_index(1)-1, -1):
            for state in range(1, self.final_state_index): # Exclude start and end states here
                for state_prime in range(1, self.final_state_index):
                    self.beta[state][time] += self.transitions[state][state_prime] *  \
                        self.emission_prob(state_prime, time+1) * \
                        self.beta[state_prime][time+1]

        # Termination
        for state in range(1, self.final_state_index):
            update = self.transitions[0][state] * self.emission_prob(state, self.get_observation_index(1)) * self.beta[state][self.get_observation_index(1)]
            self.beta[0][self.get_observation_index(1)] += update

        # # TODO: what does the backward algorithm return?
        # return self.beta[0][0]

    # zeta: the probability of being in state i and time t and state j and time t+1
    # for updating self.transitions (A)
    # i: state
    # j: state_prime
    def calc_squiggle(self, state, state_prime, time): 
        # Time should already be adjusted before calling the function
        self.zeta[state][state_prime][time] = (self.alpha[state][time] * self.transitions[state][state_prime] * \
            self.emission_prob(state_prime,time+1) * self.beta[state_prime][time+1]) / \
            self.alpha[self.final_state_index][self.final_observation_index] 

    # gamma: the probability of being in state j at time t
    # for updating self.emissions (B)
    def calc_gamma(self, state, time):
        # Time should already be adjusted before calling the function
        self.gamma[state][time] = self.alpha[state][time] * self.beta[state][time] / self.alpha[self.final_state_index][self.final_observation_index]
    
    def update_transitions(self, state, state_prime):
        # update transitions (A)
        zeta_sum_num = 0
        zeta_sum_den = 0
        
        if state == 0:
            # Start state
            self.transitions[state][state_prime] = self.gamma[state_prime][0]
        elif state_prime == self.final_state_index:
            # End state
            if (self.gamma_sum[state] != 0):
                self.transitions[state][state_prime] = self.gamma[state][self.final_observation_index] / self.gamma_sum[state]
        else:
            # For all other times
            for time in range(self.get_observation_index(1), self.observation_len):
                zeta_sum_num += self.zeta[state][state_prime][time]
                zeta_sum_den += self.gamma[state][time]

            self.gamma_sum[state] = zeta_sum_den
            self.transitions[state][state_prime] = zeta_sum_num / zeta_sum_den

    def update_emissions(self, state, v_k):
         # update emissions (B)
        gamma_sum_num = 0
        gamma_sum_den = 0

        # sum over all t for which the observation at time t was v_k
        for time in range(self.get_observation_index(1), self.observation_len):
            if self.observations[time] == v_k:
                gamma_sum_num += self.gamma[state][time]
            gamma_sum_den += self.gamma[state][time]

        if (gamma_sum_den != 0):
            self.emissions[state][v_k-1] = gamma_sum_num / gamma_sum_den

    def baum_welch(self):
        """ Given an observation sequence O and the set of states in the HMM, 
            learn the transitions and emissions of the HMM."""
        
        # iterate until convergence
        # TODO: Replace this with a while loop
        for i in range(0,1):
            old_A = self.transitions
            old_B = self.emissions
            # expectation step
            self.forward()

            self.backward()

            for time in range(self.get_observation_index(1), self.observation_len):
                for state in range(1, self.final_state_index):
                    self.calc_gamma(state,time)
            
            for time in range(self.get_observation_index(1), self.final_observation_index):
                for state in range(1, self.final_state_index):
                    for state_prime in range(1, self.final_state_index):
                        self.calc_squiggle(state,state_prime,time)

            # maximization step
            for state in range(0,self.state_len-1):
                for state_prime in range(0,self.state_len):
                    self.update_transitions(state,state_prime)

                for time in range(self.get_observation_index(1), self.final_observation_index):
                    v_k = self.observations[time]
                    self.update_emissions(state,v_k)
            
            # print "TRANSITIONS"
            # print self.transitions

            # print "EMISSIONS"
            # print self.emissions
            
            # TODO: how to determine convergence
            # print np.linalg.norm(old_A-self.transitions)
            # print np.linalg.norm(old_B-self.emissions)
                
            # return A, B
            # return self.transitions, self.emissions

    def train(self, observations, iterations=10):
        """
        Trains the model and calculates transitions and emissions probabilities
        Input = Array of ice-creams eaten each day
        """
        self.observations = observations
        self.observation_len = len(self.observations)
        self.final_observation_index = self.observation_len-1

        self.zeta = np.zeros((self.state_len, self.state_len, self.observation_len))
        self.gamma = np.zeros((self.state_len, self.observation_len))
        for i in range(0,iterations):
            # TODO: Do the Expectation Maximization step
            self.baum_welch()

    def test(self, test_observations):
        self.observations = test_observations
        self.observation_len = len(self.observations)
        self.final_observation_index = self.observation_len-1
        
        self.alpha = None
        self.beta = None
        self.zeta = None
        self.gamma = None
        self.gamma_sum = np.zeros(self.state_len)

        prob_O_lambda = self.forward()
        print "PROBABILITY"
        print prob_O_lambda 


class Recognizer(object):

    def __init__(self, audio_topic):
        rospy.init_node('recognizer')
        self.codebook = None

        self.voice_obs = None 

        # TODO: save a person's HMM after training in the recognizer

    def process_audio(self, isTraining, sound_file):
        """ Takes in a wav file and outputs labeled observations of the audio
            isTraining: bool that is true if the model is being trained
        """
        (rate, sig) = wav.read(sound_file)
        sig = sig.astype(np.float64)
        # MFCC Features. Each row corresponds to MFCC for a frame
        mfcc_feat = mfcc(sig, rate)

        labeled_obs = vq(mfcc_feat, self.codebook)[0]

        self.voice_obs = labeled_obs

    def get_mfcc_feat(self):
        # creating codebook with all models

        mfcc_feats = None
    
        for filename in glob.iglob('../data/voices/*.wav'):
            print filename
            (rate, sig) = wav.read(filename)

            # MFCC Features. Each row corresponds to MFCC for a frame
            mfcc_person = mfcc(sig.astype(np.float64), rate)

            if mfcc_feats is None:
                mfcc_feats = mfcc_person 
            else:
                mfcc_feats = np.concatenate((mfcc_feats, mfcc_person), axis=0)

        # Normalize the features
        whitened = whiten(mfcc_feats)
        self.codebook, labeled_obs = kmeans2(data=whitened, k=3)

    def run(self):
        """ Main run function """
        
        r = rospy.Rate(50) # 20 ms samples
        
        while not rospy.is_shutdown():  
            try:
                print "Running"
                r.sleep()
            except rospy.exceptions.ROSTimeMovedBackwardsException:
                print "Time went backwards. Carry on."


if __name__ == '__main__':
    states = [0, 1, 2, 3, 4, 5, 6]
    transitions = np.array([[0.0,0.2,0.2,0.2,0.2,0.2,0.0],[0.0,0.16,0.16,0.16,0.16,0.16,0.16],[0.0,0.16,0.16,0.16,0.16,0.16,0.16],[0.0,0.16,0.16,0.16,0.16,0.16,0.16],[0.0,0.16,0.16,0.16,0.16,0.16,0.16],[0.0,0.16,0.16,0.16,0.16,0.16,0.16],[0.0,0.0,0.0,0.0,0.0,0.0,0.0]])
    emissions = np.array([[0.0,0.0,0.0],[0.33,0.33,0.33],[0.33,0.33,0.33],[0.33,0.33,0.33],[0.33,0.33,0.33],[0.33,0.33,0.33],[0.0,0.0,0.0]])
    recognizer = Recognizer("/audio/audio")

    # Create a common
    recognizer.get_mfcc_feat()

    print "SHRUTI training"
    recognizer.process_audio(True, "../data/voices/shruti.wav")
    shruti = HMM(transitions=transitions, emissions=emissions, states=states)
    shruti.train(recognizer.voice_obs[100:300])
    
    print "BONNIE testing on SHRUTI"
    recognizer.process_audio(False, "../data/voices/bonnie.wav")
    shruti.test(recognizer.voice_obs[50:150])

    print "COLVIN training"
    recognizer.process_audio(True, "../data/voices/colvin.wav")
    colvin = HMM(transitions=transitions, emissions=emissions, states=states)
    colvin.train(recognizer.voice_obs[100:300])

    print "BONNIE testing on COLVIN"
    recognizer.process_audio(False, "../data/voices/bonnie.wav")
    colvin.test(recognizer.voice_obs[50:150])

    print "KATIE training"
    recognizer.process_audio(True, "../data/voices/katie.wav")
    katie = HMM(transitions=transitions, emissions=emissions, states=states)
    katie.train(recognizer.voice_obs[100:300])

    print "BONNIE testing on KATIE"
    recognizer.process_audio(False, "../data/voices/bonnie.wav")
    katie.test(recognizer.voice_obs[50:150])

    print "BONNIE training"
    recognizer.process_audio(True, "../data/voices/bonnie.wav")
    bonnie = HMM(transitions=transitions, emissions=emissions, states=states)
    bonnie.train(recognizer.voice_obs[100:300])

    print "BONNIE testing on BONNIE"
    recognizer.process_audio(False, "../data/voices/bonnie.wav")
    bonnie.test(recognizer.voice_obs[50:150])


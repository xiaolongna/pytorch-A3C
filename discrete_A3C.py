"""
Reinforcement Learning (A3C) using Pytroch + multiprocessing.
The most simple implementation for continuous action.

View more on my Chinese tutorial page [莫烦Python](https://morvanzhou.github.io/).
"""

import torch
import numpy as np
import torch.nn as nn
from utils import v_wrap, set_init
import torch.nn.functional as F
import torch.multiprocessing as mp
from shared_adam import SharedAdam
import gym

UPDATE_GLOBAL_ITER = 10
GAMMA = 0.9
MAX_EP = 4000

env = gym.make('CartPole-v0')
N_S = env.observation_space.shape[0]
N_A = env.action_space.n


class Net(nn.Module):
    def __init__(self, s_dim, a_dim):
        super(Net, self).__init__()
        self.s_dim = s_dim
        self.a_dim = a_dim
        self.pi1 = nn.Linear(s_dim, 100)
        self.pi2 = nn.Linear(100, a_dim)
        self.v1 = nn.Linear(s_dim, 100)
        self.v2 = nn.Linear(100, 1)
        set_init([self.pi1, self.pi2, self.v1, self.v2])
        self.distribution = torch.distributions.Categorical

    def forward(self, x):
        pi1 = F.relu(self.pi1(x))
        logits = self.pi2(pi1)
        v1 = F.relu(self.v1(x))
        values = self.v2(v1)
        return logits, values

    def choose_action(self, s):
        self.eval()
        logits, _ = self.forward(s)
        prob = F.softmax(logits, dim=1).data
        m = self.distribution(prob)
        return m.sample().numpy()[0]

    def loss_func(self, s, a, v_t):
        self.train()
        logits, values = self.forward(s)
        td = v_t - values
        c_loss = td.pow(2)
        
        probs = F.softmax(logits, dim=1)
        m = self.distribution(probs)
        exp_v = m.log_prob(a) * td.detach()
        a_loss = -exp_v
        total_loss = (c_loss + a_loss).mean()
        return total_loss


class Worker(mp.Process):
    def __init__(self, gnet, opt, global_ep, global_ep_r, res_queue, name):
        super(Worker, self).__init__()
        self.name = 'w%i' % name
        self.global_ep = global_ep
        self.global_ep_r = global_ep_r
        self.res_queue = res_queue
        self.gnet = gnet
        self.opt = opt
        self.lnet = Net(N_S, N_A)           # local network
        self.env = gym.make('CartPole-v0').unwrapped

    def run(self):
        total_step = 1
        while self.global_ep.value < MAX_EP:

            s = self.env.reset()
            buffer_s, buffer_a, buffer_r = [], [], []
            ep_r = 0.
            while True:
                a = self.lnet.choose_action(v_wrap(s[None, :]))
                s_, r, done, _ = self.env.step(a)
                if done: r = -1
                ep_r += r
                buffer_a.append(a)
                buffer_s.append(s)
                buffer_r.append(r)

                if total_step % UPDATE_GLOBAL_ITER == 0 or done:  # update global and assign to local net
                    if done:
                        v_s_ = 0.               # terminal
                    else:
                        v_s_ = self.lnet.forward(v_wrap(s_[None, :]))[-1].data.numpy()[0, 0]
                    buffer_v_target = []
                    for r in buffer_r[::-1]:    # reverse buffer r
                        v_s_ = r + GAMMA * v_s_
                        buffer_v_target.append(v_s_)
                    buffer_v_target.reverse()

                    buffer_s = v_wrap(np.vstack(buffer_s))
                    buffer_a = v_wrap(np.array(buffer_a), dtype=np.int64)
                    buffer_v_target = v_wrap(np.array(buffer_v_target)[:, None])

                    loss = self.lnet.loss_func(buffer_s, buffer_a, buffer_v_target)

                    # calculate local gradients and push local parameters to global
                    self.opt.zero_grad()
                    loss.backward()
                    for lp, gp in zip(self.lnet.parameters(), self.gnet.parameters()):
                        gp._grad = lp.grad
                    self.opt.step()

                    # pull global parameters
                    self.lnet.load_state_dict(self.gnet.state_dict())

                    buffer_s, buffer_a, buffer_r = [], [], []

                s = s_
                total_step += 1
                if done:                    # done and plot information
                    with self.global_ep.get_lock():
                        self.global_ep.value += 1
                    with self.global_ep_r.get_lock():
                        self.global_ep_r.value = self.global_ep_r.value*0.99 + ep_r*0.01
                    self.res_queue.put(self.global_ep_r.value)
                    print(
                        self.name,
                        "Ep:", self.global_ep.value,
                        "| Ep_r: %.0f" % self.global_ep_r.value,
                    )
                    break
        self.res_queue.put(None)


if __name__ == "__main__":
    gnet = Net(N_S, N_A)        # global network
    gnet.share_memory()         # share the global parameters in multiprocessing

    global_ep = mp.Value('i', 0)
    global_ep_r = mp.Value('d', 0.)
    res_queue = mp.Queue()
    opt = SharedAdam(gnet.parameters(), lr=0.0001)      # global optimizer

    # parallel training
    workers = [Worker(gnet, opt, global_ep, global_ep_r, res_queue, i) for i in range(4)]
    [w.start() for w in workers]
    res = []                    # record episode reward to plot
    while True:
        r = res_queue.get()
        if r is not None:
            res.append(r)
        else:
            break
    [w.join() for w in workers]

    import matplotlib.pyplot as plt
    plt.plot(res)
    plt.ylabel('Moving average ep reward')
    plt.xlabel('Step')
    plt.show()

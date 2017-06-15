import os
import torch
from torch.autograd import Variable
from torch import optim
import torch.nn.functional as F
import torch.nn as nn
import numpy as np
import models
import argparse
import time
import math
from tqdm import tqdm

parser = argparse.ArgumentParser(description='lm.py')

parser.add_argument('-save_model', default='lm',
                    help="""Model filename to save""")
parser.add_argument('-load_model', default='',
                    help="""Model filename to load""")
parser.add_argument('-train', default='data/input.txt',
                    help="""Text filename for training""")
parser.add_argument('-valid', default='data/valid.txt',
                    help="""Text filename for validation""")                    
parser.add_argument('-rnn_type', default='gru',
                    help='Number of layers in the encoder/decoder')
parser.add_argument('-layers', type=int, default=1,
                    help='Number of layers in the encoder/decoder')
parser.add_argument('-rnn_size', type=int, default=1024,
                    help='Size of hidden states')
parser.add_argument('-embed_size', type=int, default=128,
                    help='Size of embeddings')
parser.add_argument('-seq_length', type=int, default=20,
                    help="Maximum sequence length")
parser.add_argument('-batch_size', type=int, default=64,
                    help='Maximum batch size')
parser.add_argument('-learning_rate', type=float, default=0.001,
                    help="""Starting learning rate.""")
parser.add_argument('-dropout', type=float, default=0.1,
                    help='Dropout probability.')
parser.add_argument('-param_init', type=float, default=0.05,
                    help="""Parameters are initialized over uniform distribution
                    with support (-param_init, param_init)""")
parser.add_argument('-clip', type=float, default=5,
                    help="""Clip gradients at this value.""")
parser.add_argument('--seed', type=int, default=1234,
                    help='random seed')   
# GPU
parser.add_argument('-cuda', action='store_true',
                    help="Use CUDA")


parser.add_argument('-ns', action='store_true',
                    help="Load open AI mlstm weights from numpy files")                 

opt = parser.parse_args()    



path = opt.train
torch.manual_seed(opt.seed)
if opt.cuda:
	torch.cuda.manual_seed(opt.seed)

TIMESTEPS = opt.seq_length

def tokenize(path):
        """Tokenizes a text file."""
        assert os.path.exists(path)
        # Count bytes
        with open(path, 'r') as f:
            tokens = 0
            nlines = 0
            for line in f:              
                tokens += len(line)
                nlines += 1
                
        print(tokens, nlines)
        # Tokenize file content
        with open(path, 'r') as f:
            ids = torch.ByteTensor(nlines*TIMESTEPS)
            ids[:] = 0
            token = 0
            for line in tqdm(f,total=nlines):
                for char in line[:TIMESTEPS]:
                    ids[token] = ord(char)
                    token += 1

        return ids

def batchify(data, bsz):
    nbatch = data.size(0) // bsz
    data = data.narrow(0, 0, nbatch * bsz)
    data = data.view(bsz, -1).t().contiguous()
    return data        



batch_size = opt.batch_size
hidden_size =opt.rnn_size
input_size = opt.embed_size
data_size = 256

if len(opt.load_model)>0:
    checkpoint = torch.load(opt.load_model)
    embed = checkpoint['embed']
    rnn = checkpoint['rnn']
else:
    embed = nn.Embedding(256, input_size)
    if opt.rnn_type == 'gru':
    	rnn = models.StackedRNN(nn.GRUCell, opt.layers, input_size, hidden_size, data_size, opt.dropout)
    elif opt.rnn_type == 'mlstm':
    	rnn = models.StackedLSTM(models.mLSTM, opt.layers, input_size, hidden_size, data_size, opt.dropout)
    else:#default to lstm
    	rnn = models.StackedLSTM(nn.LSTMCell, opt.layers, input_size, hidden_size, data_size, opt.dropout)

    if opt.ns:
    	embed.weight.data = torch.from_numpy(np.load("embd.npy"))
    	rnn.h2o.weight.data = torch.from_numpy(np.load("w.npy")).t()
    	rnn.h2o.bias.data = torch.from_numpy(np.load("b.npy"))
    	rnn.layers[0].wx.weight.data = torch.from_numpy(np.load("wx.npy")).t()
    	rnn.layers[0].wh.weight.data = torch.from_numpy(np.load("wh.npy")).t()
    	rnn.layers[0].wh.bias.data = torch.from_numpy(np.load("b0.npy"))
    	rnn.layers[0].wmx.weight.data = torch.from_numpy(np.load("wmx.npy")).t()
    	rnn.layers[0].wmh.weight.data = torch.from_numpy(np.load("wmh.npy")).t()

loss_fn = nn.CrossEntropyLoss() 

nParams = sum([p.nelement() for p in rnn.parameters()])
print('* number of parameters: %d' % nParams)
text = tokenize(path)
text = batchify(text, batch_size)
valid = tokenize(opt.valid)
valid = batchify(valid, batch_size)

learning_rate =opt.learning_rate

n_batch = text.size(0)//TIMESTEPS
nv_batch = valid.size(0)//TIMESTEPS

print(text.size(0))
print(n_batch)
embed_optimizer = optim.SGD(embed.parameters(), lr=learning_rate)
rnn_optimizer = optim.SGD(rnn.parameters(), lr=learning_rate)
   

def clip_gradient_coeff(model, clip):
    """Computes a gradient clipping coefficient based on gradient norm."""
    totalnorm = 0
    for p in model.parameters():
        modulenorm = p.grad.data.norm()
        totalnorm += modulenorm ** 2
    totalnorm = math.sqrt(totalnorm)
    return min(1, clip / (totalnorm + 1e-6))

def calc_grad_norm(model):
    """Computes a gradient clipping coefficient based on gradient norm."""
    totalnorm = 0
    for p in model.parameters():
        modulenorm = p.grad.data.norm()
        totalnorm += modulenorm ** 2
    return math.sqrt(totalnorm)
    
def calc_grad_norms(model):
    """Computes a gradient clipping coefficient based on gradient norm."""
    norms = []
    for p in model.parameters():
        modulenorm = p.grad.data.norm()
        norms += [modulenorm]
    return norms
    
def clip_gradient(model, clip):
    """Clip the gradient."""
    totalnorm = 0
    for p in model.parameters():
        p.grad.data = p.grad.data.clamp(-clip,clip)

        
def make_cuda(state):
    if isinstance(state, tuple):
    	return (state[0].cuda(), state[1].cuda())
    else:
    	return state.cuda()
    	
def copy_state(state):
    if isinstance(state, tuple):
    	return (Variable(state[0].data), Variable(state[1].data))
    else:
    	return Variable(state.data)    	


def evaluate():
    hidden_init = rnn.state0(opt.batch_size)  		
    if opt.cuda:
	    embed.cuda()
	    rnn.cuda()
	    hidden_init = make_cuda(hidden_init)

    loss_avg = 0
    for s in range(nv_batch-1):
        batch = Variable(valid.narrow(0,s*TIMESTEPS,TIMESTEPS+1).long())
        start = time.time()
        hidden = hidden_init
        if opt.cuda:
            batch = batch.cuda()

        loss = 0
        for t in range(TIMESTEPS):                  
            emb = embed(batch[t])
            hidden, output = rnn(emb, hidden)
            loss += loss_fn(output, batch[t+1])

        hidden_init = copy_state(hidden)
        loss_avg = loss_avg + loss.data[0]/TIMESTEPS
        if s % 10 == 0:
            print('v %s / %s loss %.4f loss avg %.4f time %.4f' % ( s, nv_batch, loss.data[0]/TIMESTEPS, loss_avg/(s+1), time.time()-start))
    return loss_avg/nv_batch

def train_epoch(epoch):
	hidden_init = rnn.state0(opt.batch_size)    		
	if opt.cuda:
	    embed.cuda()
	    rnn.cuda()
	    hidden_init = make_cuda(hidden_init)

	loss_avg = 0

	for s in range(n_batch-1):

		embed_optimizer.zero_grad()
		rnn_optimizer.zero_grad()
		batch = Variable(text.narrow(0,s*TIMESTEPS,TIMESTEPS+1).long())
		start = time.time()
		hidden = hidden_init
		if opt.cuda:
			batch = batch.cuda()
		loss = 0
		for t in range(TIMESTEPS):                  
			emb = embed(batch[t])
			hidden, output = rnn(emb, hidden)
			loss += loss_fn(output, batch[t+1])
        
        
		loss.backward()
    
		hidden_init = copy_state(hidden)
		gn =calc_grad_norm(rnn)
		clip_gradient(rnn, opt.clip)
		clip_gradient(embed, opt.clip)
		embed_optimizer.step()
		rnn_optimizer.step()
		loss_avg = .99*loss_avg + .01*loss.data[0]/TIMESTEPS
		if s % 10 == 0:
			print('e%s %s / %s loss %.4f loss avg %.4f time %.4f grad_norm %.4f' % (epoch, s, n_batch, loss.data[0]/TIMESTEPS, loss_avg, time.time()-start, gn))
        if s % (n_batch // 10) == 0:
            checkpoint = {
                'rnn': rnn,
                'embed': embed,
                'opt': opt,
            }
            torch.save(checkpoint, 'temp')


for e in range(10):
	try:
		train_epoch(e)
	except KeyboardInterrupt:
		print('Exiting from training early')
	loss_avg = evaluate()
	checkpoint = {
            'rnn': rnn,
            'embed': embed,
            'opt': opt,
            'epoch': e
        }
	save_file = ('%s_e%s_%.2f.pt' % (opt.save_model, e, loss_avg))
	print('Saving to '+ save_file)
	torch.save(checkpoint, save_file)
	opt.learning_rate *= 0.7

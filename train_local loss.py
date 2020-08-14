import os
import time
import argparse
import torch
import math
import torch.nn as nn
from torch.nn import init
import pandas as pd
import matplotlib.pyplot as plt
from tensorboardX import SummaryWriter
from torch.autograd import Variable
import numpy as np
import tqdm as tqdm
import torch.nn.functional as F
import  torchsnooper as torchsnooper
import torch.optim as optim
from sklearn.metrics import precision_score, recall_score, f1_score,accuracy_score,confusion_matrix
from torchvision import datasets, transforms
from torch.backends import cudnn
from bisect import bisect_right
import torch.utils.data as Data
from tqdm import tqdm
from module_uci_har import LegoConv2d

import os
torch.cuda.set_device(1)
os.environ["CUDA_VISIBLE_DEVICES"] = "0"
n_gpu = torch.cuda.device_count()
print(n_gpu)
path=os.path.dirname(os.path.abspath("__file__"))
print(path)
pathlist = ['/home/tangyin/桌面/emnist/LegoNet-master/data_uci/uci_har/np_train_x.npy',
            '/home/tangyin/桌面/emnist/LegoNet-master/data_uci/uci_har/np_train_y.npy',
            '/home/tangyin/桌面/emnist/LegoNet-master/data_uci/uci_har/np_test_x.npy',
            '/home/tangyin/桌面/emnist/LegoNet-master/data_uci/uci_har/np_test_y.npy',
            ]


# this is UCIdataset. torch.Size([7352,128, 9]) torch.Size([7352]) windows size:128 channel:9 calss:6 overlap:50%



# # @torchsnooper.snoop()
def data_flat(data_y):
    data_y=np.argmax(data_y, axis=1)
    return data_y


def load_data(train_x_path, train_y_path, batchsize):
    train_x = np.load(train_x_path)
    train_x_shape = train_x.shape
    train_x = torch.from_numpy(
        np.reshape(train_x.astype(float), [train_x_shape[0],1, train_x_shape[1], train_x_shape[2]])).cuda()

    train_y = data_flat(np.load(train_y_path))
    train_y = torch.from_numpy(train_y).cuda()

    print('^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n',
          train_x.shape, train_y.shape,
          '\n^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^')
    torch_dataset = Data.TensorDataset(train_x, train_y)
    loader = Data.DataLoader(
        dataset=torch_dataset,
        batch_size=batchsize,
        shuffle=True,
        num_workers=0,
    )
    total = len(loader)
    # for _ in tqdm(range(total), desc='进行中', ncols=80,postfix="train_data"):
    #     pass
    return loader

def similarity_matrix(x):
    ''' Calculate adjusted cosine similarity matrix of size x.size(0) x x.size(0). '''
    if x.dim() == 4:
        if x.size(1) > 3 and x.size(2) > 1:
            z = x.view(x.size(0), x.size(1), -1)
            x = z.std(dim=2)
            # print('this similarity matrix x shape',x.shape)
        else:
            x = x.view(x.size(0), -1)
    xc = x - x.mean(dim=1).unsqueeze(1)
    xn = xc / (1e-8 + torch.sqrt(torch.sum(xc ** 2, dim=1))).unsqueeze(1)
    R = xn.matmul(xn.transpose(1, 0)).clamp(-1, 1)

    return R

def quzheng_x(height,kernel_size,padding,stride,numlayer):
    list=[]
    for i in range(1,numlayer+1):
        feature=int((height-kernel_size+2*padding)/stride)+1
        height=feature
        list.append(feature)
    return list

def quzheng_s(height,kernel_size,padding,stride,numlayer):
    list=[]
    for i in range(1,numlayer+1):
        feature=math.ceil((height-kernel_size+2*padding)/stride)+1
        height=feature
        list.append(feature)
    return list

class Loss(nn.Module):
    def __init__(self):
        super(Loss, self).__init__()
        print('this is loss function!')

    def forward(self, output, label):
        loss_func = F.cross_entropy(output, label)
        return loss_func

class conv_loss_blockc(nn.Module):
    def __init__(self, channel_in, channel_out,stride):
        super(conv_loss_blockc, self).__init__()
        self.channel_in = channel_in
        self.channel_out = channel_out
        self.decode_ys = []
        self.bns_decode_ys = []


        decode_t_list = [15872, 15360, 14848, 14336]  # pool_size[2,2]
        decode_t_list = [31744, 28672, 24576, 35840]#pool_size[1,2]
        decode_t_list = [46848, 28672, 12288, 35840]  # pool_size[1,2]
        decode_t_list = [7680, 3072, 3072, 35840]  # pool_size[1,2]
        decode_t_list = [30720, 12288, 3072, 35840]  # pool_size[1,2]
        decode_t_list = [29568, 21632, 15360, 35840]  # pool_size[1,2]
        # print(int(channel_out*height_width*0.5),'self.biasself.biasself.biasself.biasself.bias')
        # print(channel_in, channel_out,'channel_in, channel_out, height_width, kernel, stride, bias')

        self.encoder =nn.Sequential(nn.Conv2d(self.channel_in, self.channel_out, (6, 1),stride=stride,padding=1),
                                    nn.BatchNorm2d(self.channel_out),
                                    nn.ReLU(inplace=True),

                                    )
        self.avg_pool = nn.MaxPool2d((2, 2),stride=1)

        for i in range(3):
            decode_y = nn.Linear(decode_t_list[i], 6)
            setattr(self, 'decode_y%i' % i, decode_y)
            self._set_init(decode_y)
            self.decode_ys.append(decode_y)

        # self.decode_y = nn.Linear(4096, self.num_class)
        # # print(channel_out * height_width,'channel_out * height_width')
        # self.decode_y.weight.data.zero_()

        self.conv_loss = nn.Conv2d(self.channel_out, self.channel_out, kernel_size=(3, 3), stride=(1, 1),
                                   padding=(1, 1), bias=False)

        if True:
            self.bn = torch.nn.BatchNorm2d(self.channel_out, momentum=0.5)
            nn.init.constant_(self.bn.weight, 1)
            nn.init.constant_(self.bn.bias, 0)

        self.nonlin = nn.ReLU(inplace=True)
        self.dropout = torch.nn.Dropout(p=0.5, inplace=False)
        self.optimizer = optim.Adam(self.parameters(), lr=0.001, amsgrad=False)

        self.clear_stats()

    def _set_init(self, layer):
        init.normal_(layer.weight, mean=0., std=.1)
        init.constant_(layer.bias, 0.2)

    def clear_stats(self):
        self.loss_sim = 0.0
        self.loss_pred = 0.0
        self.correct = 0
        self.examples = 0

    def print_stats(self):
        stats = '{}, loss_sim={:.4f}, loss_pred={:.4f}, error={:.3f}%, num_examples={}\n'.format(
            self.encoder,
            self.loss_sim / self.examples,
            self.loss_pred / self.examples,
            100.0 * float(self.examples - self.correct) / self.examples,
            self.examples)
        return stats

    def set_learning_rate(self, lr):
        self.lr = lr

        for param_group in self.optimizer.param_groups:
            param_group['lr'] = self.lr

    def optim_zero_grad(self):
        self.optimizer.zero_grad()

    def optim_step(self):
        # print('下一步优化网络')
        self.optimizer.step()

    def forward(self, x, y, y_onehot, loop, is_training):
        # print(x.shape,'xxxxxxxxxxxx')
        h = self.encoder(x)
        # print(h.shape,'h.shapeh.shapeh.shapeh.shapeh.shapeh.shapeh.shapeh.shapeh.shapeh.shapeh.shapeh.shapeh.shapeh.shapeh.shape')


        h_return = h
        h_shape = h.shape
        # print(h.shape,'h.shapeh.shapeh.shapeh.shapeh.shapeh.shape')
        h_return = self.dropout(h_return)

        #####先省略pooling#######
        h_loss = self.conv_loss(h_return)
        # print(h_loss.shape,y_onehot.shape,'h_loss.shape,y_onehot.shape')
        Rh = similarity_matrix(h_loss)

        # caculate unsupervised loss
        Rx = similarity_matrix(x).detach()
        loss_unsup = F.mse_loss(Rh, Rx)

        # h_pool = self.avg_pool(h_return)iiiij
        h_pool = h_return
        # print(h_pool.shape,'h_pool.view(h_pool.size(0)')

        y_hat_local = self.decode_ys[loop](h_pool.view(h_pool.size(0), -1))
        # print(y_hat_local.shape, y.shape,y_onehot.shape,'y_hat_local.shape, y.detach().shape')
        loss_pred = (1 - 0.99) * F.cross_entropy(y_hat_local, y.detach().long())

        Ry = similarity_matrix(y_onehot).detach()
        loss_sim = 0.99* F.mse_loss(Rh, Ry)

        # print(loss_sim,'loss_simloss_simloss_simloss_simloss_simloss_simloss_simloss_sim')
        loss_sup = loss_pred + loss_sim
        # loss = Variable(loss_pred+loss_sim, requires_grad=True)

        loss = loss_sup * 1 + loss_unsup * 0

        if is_training:
            # print(self.training,'局部反向传播')
            loss.backward(retain_graph=False)
        if is_training:
            self.optimizer.step()
            self.optimizer.zero_grad()
            h_return.detach_()
        loss = loss.item()

        return h_return, loss


class conv_loss_blockl(nn.Module):
    def __init__(self, channel_in, channel_out, n_split, n_lego, stride):
        super(conv_loss_blockl, self).__init__()
        self.channel_in = channel_in
        self.channel_out = channel_out
        self.n_split, self.n_lego = n_split, n_lego
        self.decode_ys = []
        self.bns_decode_ys = []


        decode_t_list = [15872, 15360, 14848, 14336]  # pool_size[2,2]
        decode_t_list = [31744, 28672, 24576, 35840]#pool_size[1,2]
        decode_t_list = [46848, 28672, 12288, 35840]  # pool_size[1,2]
        decode_t_list = [7680, 3072, 3072, 35840]  # pool_size[1,2]
        decode_t_list = [30720, 12288, 3072, 35840]  # pool_size[1,2]
        decode_t_list = [29568, 21632, 15360, 35840]  # pool_size[1,2]
        # print(int(channel_out*height_width*0.5),'self.biasself.biasself.biasself.biasself.bias')
        # print(channel_in, channel_out,'channel_in, channel_out, height_width, kernel, stride, bias')

        self.encoder = nn.Sequential(LegoConv2d(self.channel_in, self.channel_out, 6, self.n_split, self.n_lego),
                                    nn.BatchNorm2d(self.channel_out),
                                    nn.ReLU(inplace=True),

                                    )
        self.avg_pool = nn.MaxPool2d((2, 2),stride=1)

        for i in range(3):
            decode_y = nn.Linear(decode_t_list[i], 6)
            setattr(self, 'decode_y%i' % i, decode_y)
            self._set_init(decode_y)
            self.decode_ys.append(decode_y)

        # self.decode_y = nn.Linear(4096, self.num_class)
        # # print(channel_out * height_width,'channel_out * height_width')
        # self.decode_y.weight.data.zero_()

        self.conv_loss = nn.Conv2d(self.channel_out, self.channel_out, kernel_size=(3, 3), stride=(1, 1),
                                   padding=(1, 1), bias=False)

        if True:
            self.bn = torch.nn.BatchNorm2d(self.channel_out, momentum=0.5)
            nn.init.constant_(self.bn.weight, 1)
            nn.init.constant_(self.bn.bias, 0)

        self.nonlin = nn.ReLU(inplace=True)
        self.dropout = torch.nn.Dropout(p=0.5, inplace=False)
        self.optimizer = optim.Adam(self.parameters(), lr=0.001, amsgrad=False)

        self.clear_stats()

    def _set_init(self, layer):
        init.normal_(layer.weight, mean=0., std=.1)
        init.constant_(layer.bias, 0.2)

    def clear_stats(self):
        self.loss_sim = 0.0
        self.loss_pred = 0.0
        self.correct = 0
        self.examples = 0

    def print_stats(self):
        stats = '{}, loss_sim={:.4f}, loss_pred={:.4f}, error={:.3f}%, num_examples={}\n'.format(
            self.encoder,
            self.loss_sim / self.examples,
            self.loss_pred / self.examples,
            100.0 * float(self.examples - self.correct) / self.examples,
            self.examples)
        return stats

    def set_learning_rate(self, lr):
        self.lr = lr

        for param_group in self.optimizer.param_groups:
            param_group['lr'] = self.lr

    def optim_zero_grad(self):
        self.optimizer.zero_grad()

    def optim_step(self):
        # print('下一步优化网络')
        self.optimizer.step()

    def forward(self, x, y, y_onehot, loop, is_training):
        # print(x.shape,'xxxxxxxxxxxx')
        h = self.encoder(x)
        # print(h.shape,'h.shapeh.shapeh.shapeh.shapeh.shapeh.shapeh.shapeh.shapeh.shapeh.shapeh.shapeh.shapeh.shapeh.shapeh.shape')


        h_return = h
        h_shape = h.shape
        # print(h.shape,'h.shapeh.shapeh.shapeh.shapeh.shapeh.shape')
        h_return = self.dropout(h_return)

        #####先省略pooling#######
        h_loss = self.conv_loss(h_return)
        # print(h_loss.shape,y_onehot.shape,'h_loss.shape,y_onehot.shape')
        Rh = similarity_matrix(h_loss)

        # caculate unsupervised loss
        Rx = similarity_matrix(x).detach()
        loss_unsup = F.mse_loss(Rh, Rx)

        # h_pool = self.avg_pool(h_return)iiiij
        h_pool = h_return
        # print(h_pool.shape,'h_pool.view(h_pool.size(0)')

        y_hat_local = self.decode_ys[loop](h_pool.view(h_pool.size(0), -1))
        # print(y_hat_local.shape, y.shape,y_onehot.shape,'y_hat_local.shape, y.detach().shape')
        loss_pred = (1 - 0.99) * F.cross_entropy(y_hat_local, y.detach().long())

        Ry = similarity_matrix(y_onehot).detach()
        loss_sim = 0.99 * F.mse_loss(Rh, Ry)

        # print(loss_sim,'loss_simloss_simloss_simloss_simloss_simloss_simloss_simloss_sim')
        loss_sup = loss_pred + loss_sim
        # loss = Variable(loss_pred+loss_sim, requires_grad=True)

        loss = loss_sup * 1 + loss_unsup * 0

        if is_training:
            # print(self.training,'局部反向传播')
            loss.backward(retain_graph=False)
        if is_training:
            self.optimizer.step()
            self.optimizer.zero_grad()
            h_return.detach_()
        loss = loss.item()

        return h_return, loss


class convnet(nn.Module):
    def __init__(self, input_ch, output_ch, num_layers, num_classes):
        super(convnet, self).__init__()
        self.num_layers = num_layers
        self.input_ch = input_ch
        self.output_ch = output_ch
        self.bn = []
        self.layers = nn.ModuleList(
            [conv_loss_blockc(self.input_ch, self.output_ch, stride=(3, 1))])
        self.layers.extend(
            [conv_loss_blockl(64, 128, 4, 0.5, stride=(3, 1)),
             conv_loss_blockl(128, 256, 4, 0.5, stride=(3, 1))
             ])

        self.layer_out = nn.Linear(15360, num_classes)

        self.layer_out.weight.data.zero_()
        # self.layer_out = nn.Sequential(
        #                                nn.Linear(64512, 128,bias=True),
        #                                nn.Dropout2d(0.5),
        #                                nn.ReLU(),
        #                                nn.Linear(128, 128, bias=True),
        #                                nn.Dropout2d(0.5),
        #                                nn.ReLU(),
        #                                nn.Linear(128,6,bias=True)
        # )

        # self.layer_out.weight.data.zero_()

        bn = nn.BatchNorm2d(1, momentum=0.5)
        setattr(self, 'pre_bn', bn)
        self.bn.append(bn)

    def parameters(self):
        return self.layer_out.parameters()

    def set_learning_rate(self, lr):
        for i, layer in enumerate(self.layers):
            layer.set_learning_rate(lr)

    def optim_step(self):
        for i, layer in enumerate(self.layers):
            # print('下一步优化')
            layer.optim_step()

    def optim_zero_grad(self):
        for i, layer in enumerate(self.layers):
            # print('初始化optim')
            layer.optim_zero_grad()

    def forward(self, x, y, y_onehot, is_training):
        # x = x.view(x.size(0), -1)

        total_loss = 0.0
        for i, layer in enumerate(self.layers):
            # print(x.shape, 'x.shape')
            if i == 0:
                x = x.type(torch.cuda.FloatTensor)
                # print(x.shape,'x.shape')
                x = self.bn[i](x)
            # print(x.shape, 'x.shape')
            # print('|||||||||||||||||||||||||||||||||||||||||||||||||||||||||||')

            x, loss = layer(x, y, y_onehot, i, is_training)
            # print('|||||||||||||||||||||||||||||||||||||||||||||||||||||||||||')
            total_loss += loss
            # print(x.shape,'xrrrrrrrr',i)

        x = x.contiguous().view(x.size(0), -1)
        x = self.layer_out(x)

        return x, total_loss

def to_one_hot(y, n_dims=None):
    ''' Take integer tensor y with n dims and convert it to 1-hot representation with n+1 dims. '''
    y_tensor = y.type(torch.LongTensor).view(-1, 1)
    n_dims = n_dims if n_dims is not None else int(torch.max(y_tensor)) + 1
    y_one_hot = torch.zeros(y_tensor.size()[0], n_dims).scatter_(1, y_tensor, 1)
    y_one_hot = y_one_hot.view(*y.shape, -1)
    return y_one_hot


def adjust_learning_rate(optimizer, epoch):
  lr = 0.001 * (0.1 ** (epoch // 100))
  for param_group in optimizer.param_groups:
    param_group['lr'] = lr


# input_ch,output_ch,height,num_layers,num_hiden,num_classes

def train(train_loader, test_x_path, test_y_path,train_error,test_error,accuracy_list,epoch):
    model.train()
    correct = 0
    loss_total_local = 0
    loss_total_global = 0

    # total = sum([param.nelement() for param in model.parameters()])
    # print('  + Number of params: %.3fM' % (total / 1e6),flops, params)
    total_num = sum(p.numel() for p in model.parameters())
    trainable_num = sum(p.numel() for p in model.parameters() if p.requires_grad)

    # for m in model.modules():
    #     m.clear_stats()

    print('Total_Number of params: {} |Trainable_num of params: {} '.format(total_num, trainable_num))
    for step, (batch_x, batch_y) in enumerate(train_loader):
        batch_x, batch_y = batch_x.cuda(), batch_y.cuda()
        target_onehot = to_one_hot(batch_y)
        target_onehot = target_onehot.cuda()

        # print(batch_x.shape,target_onehot.shape,batch_y.shape,'batch_x,target_onehot.shape,batch_y.shape')
        # check_parameters(model,2)

        optimizer.zero_grad()
        output,loss= model(batch_x,batch_y, target_onehot,True)
        loss_total_local += loss * batch_x.size(0)

        # print(output.shape,batch_y.shape,target_onehot.shape,'output.shape')
        #
        loss = loss_func (output, batch_y.long())
        # loss_total_global += loss.item() * batch_x.size(0)
        # check_parameters(model, 2)


        # loss_t.backward()
        loss.backward()
        optimizer.step()
        # model.optim_step()
    # loss_average_local = loss_total_local / len(train_loader.dataset)
    # loss_average_global = loss_total_global / len(train_loader.dataset)
    # error_percent = 100 - 100.0 * float(correct) / len(train_loader.dataset)
    # string_print = 'Train epoch={},  loss_local={:.4f}, loss_global={:.4f}, error={:.3f}%, mem={:.0f}MiB, max_mem={:.0f}MiB\n'.format(
    #     epoch,
    #     loss_average_local,
    #     loss_average_global,
    #     error_percent,
    #     torch.cuda.memory_allocated()/1e6,
    #     torch.cuda.max_memory_allocated()/1e6)
    # print(string_print)
        # if epoch%10==0:
        # print('局部更新')
        # check_parameters(model, 2)
    # check_parameters(model, 16)
    # params = list(model.named_parameters())
    # (name, param) = params[11]
    # print('___________________________________________________________________\n', name, param,
    #       '\n____________________________________________________________________')
    # if epoch % 6 == 0:
    #     train_output = torch.max(output, 1)[1].cuda()
    #     taccuracy = (torch.sum(train_output == batch_y.long()).type(torch.FloatTensor) / batch_y.size(0)).cuda()
    #
    #
    #     error=1-taccuracy.item()
    #     train_error.append(error)
    #     print('epoch:',epoch,'accuracy:',taccuracy.item())
    #
    #     # np.save('./matplotlib_picture/UCI_error/predsim_train.npy', train_error)

    if epoch % 1 == 0:
        model.eval()

        test_x = np.load(test_x_path)
        test_x_shape = test_x.shape
        test_x = torch.from_numpy(np.reshape(test_x, [test_x_shape[0], 1, test_x_shape[1], test_x_shape[2]])).cuda()

        test_y = data_flat(np.load(pathlist[3]))
        test_y = torch.from_numpy(test_y).cuda()

        test_y_onehot = to_one_hot(test_y)
        test_y_onehot = test_y_onehot.cuda()

        # print(test_x.shape, test_y.shape, test_y_onehot.shape, 'test_x.shape,test_y.shape,target_y.shape')
        try:
            test_output,_ = model(test_x,test_y, test_y_onehot,False)
            test_output_copy = test_output
            # print(test_output.shape)
            test_output = data_flat(test_output.cpu().detach().numpy())
            # print(test_output.shape, 'test_output')

            test_output_f1 = np.asarray(pd.get_dummies(test_output))

            print(test_y_onehot.shape, test_output_f1.shape)
            acc = accuracy_score(test_y_onehot.cpu().numpy(), test_output_f1)
            f1 = f1_score(test_y_onehot.cpu().numpy(), test_output_f1, average='weighted')
            f2 = f1_score(test_y_onehot.cpu().numpy(), test_output_f1, average='micro')
            f3 = f1_score(test_y_onehot.cpu().numpy(), test_output_f1, average='macro')
            reca = recall_score(test_y_onehot.cpu().numpy(), test_output_f1, average='weighted')
            # print(confusion_y.tolist(), '\n', test_output_f1_con.tolist())
            print('Epoch: ', epoch, 'lr:', optimizer.param_groups[0]['lr'], '\n', '| test accuracy: %.8f' % acc, '| test F1: %.8f' % f1, '| test recall: %.8f' % reca,
                  '| test micro: %.8f' % f2, '| test micro: %.8f' % f3)
            #
            test_output = torch.max(test_output_copy, 1)[1].cuda()
            # print(test_output.shape,'test_output.shape')
            accuracy = (torch.sum(test_output == test_y.long()).type(torch.FloatTensor) / test_y.size(0)).cuda()
            # print('Epoch: ', epoch, '| test accuracy: %.8f' % accuracy)
            test_error.append((1 - accuracy.item()))
            # np.save("/home/tangyin/桌面/emnist/LegoNet-master/data_uci/uci_har/local55_loss.npy", test_error)
            model.train()
        except ValueError:
            print('error')
        else:
            pass

    # np.save('./matplotlib_picture/UCI_error/bp_test.npy',test_error)


if __name__ == '__main__':

        model = convnet(1, 64, 3, 6)
        model.cuda()
        print(model)

        optimizer = torch.optim.Adam(model.parameters(), lr=0.0001)
        loss_func = nn.CrossEntropyLoss().cuda()
        train_loader = load_data(pathlist[0], pathlist[1], batchsize=200)
        # model.set_learning_rate(6e-4)
        # writer = SummaryWriter()
        train_error=[]
        test_error=[]
        accuracy_list=[]
        # lr=[0.004,0.001,0.0009,0.0007,0.0005]
        # lr = [0.001, 0.0008, 0.0007, 0.0006, 0.0003]
        for epoch in range(500):
            # if epoch<=50:
            #     lr_dynamic=lr[0]
            #     model.set_learning_rate(lr_dynamic)
            # elif 51<=epoch<=120:
            #     lr_dynamic=lr[1]
            #     model.set_learning_rate(lr_dynamic)
            # elif 121<=epoch<=200:
            #     lr_dynamic=lr[2]
            #     model.set_learning_rate(lr_dynamic)
            # elif 201<=epoch<=400:
            #     lr_dynamic=lr[3]
            #     model.set_learning_rate(lr_dynamic)
            # elif 401<=epoch:
            #     lr_dynamic=lr[4]
            #     model.set_learning_rate(lr_dynamic)
            train(train_loader, pathlist[2], pathlist[3],train_error,test_error,accuracy_list,epoch)
            # adjust_learning_rate(optimizer, epoch)
        # writer.close()

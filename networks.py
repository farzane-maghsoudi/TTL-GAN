import torch
import torch.nn as nn
from torch.nn.parameter import Parameter
from utils import resize2d

class ResnetGenerator(nn.Module):
    def __init__(self, input_nc, output_nc, ngf=64, n_blocks=100, img_size=256, light=False):
        assert(n_blocks >= 0)
        super(ResnetGenerator, self).__init__()
        self.input_nc = input_nc
        self.output_nc = output_nc
        self.ngf = ngf
        self.n_blocks = n_blocks
        self.img_size = img_size
        self.light = light

        n_downsampling = 2

        mult = 2**n_downsampling
        UpBlock0 = [nn.ReflectionPad2d(1),
                nn.Conv2d(int(ngf * mult / 2), ngf * mult, kernel_size=3, stride=1, padding=0, bias=True),
                ILN(ngf * mult),
                nn.ReLU(True)]

        self.relu = nn.ReLU(True)

        # Gamma, Beta block
        if self.light:
            FC = [nn.Linear(ngf * mult, ngf * mult, bias=False),
                  nn.ReLU(True),
                  nn.Linear(ngf * mult, ngf * mult, bias=False),
                  nn.ReLU(True)]
        else:
            FC = [nn.Linear(img_size // mult * img_size // mult * ngf * mult, ngf * mult, bias=False),
                  nn.ReLU(True),
                  nn.Linear(ngf * mult, ngf * mult, bias=False),
                  nn.ReLU(True)]
        self.gamma = nn.Linear(ngf * mult, ngf * mult, bias=False)
        self.beta = nn.Linear(ngf * mult, ngf * mult, bias=False)

        # Up-Sampling Bottleneck
        self.atten = torch.nn.MultiheadAttention(ngf, 8)
        self.attrelu = nn.ReLU(True)
        self.attnorm = adaILN(ngf * mult)
        self.attconv = nn.Conv2d(ngf * mult, ngf * mult, kernel_size=3, stride=1, padding=1, bias=True)
        #self.attention = MultiSelfAttentionBlock(dim = ngf, featur= ngf * mult, n_channel = 8)
        conv_block = [nn.ReflectionPad2d(1),
                       nn.Conv2d(ngf * mult, ngf * mult, kernel_size=3, stride=1, padding=0, bias=False),
                       nn.LeakyReLU(0.2, True)] 
        conv_block1 = [nn.ReflectionPad2d(1), nn.Conv2d(ngf * mult, ngf * mult, kernel_size=3, stride=1, padding=0, bias=False)] 
        
        #for i in range(n_blocks):
        #    setattr(self, 'UpBlock1_' + str(i+1), ResnetAdaILNBlock(ngf * mult, use_bias=False))

        # Up-Sampling. Two paths for decoding.
        UpBlock2 = []
        UpBlock3 = []
        UpBlock4 = []
        for i in range(n_downsampling):
            mult = 2**(n_downsampling - i)
            # Experiments show that the performance of Up-sample and Sub-pixel is similar,
            #  although theoretically Sub-pixel has more parameters and less FLOPs.
            # UpBlock2 += [nn.Upsample(scale_factor=2, mode='nearest'),
            #              nn.ReflectionPad2d(1),
            #              nn.Conv2d(ngf * mult, int(ngf * mult / 2), kernel_size=3, stride=1, padding=0, bias=False),
            #              ILN(int(ngf * mult / 2)),
            #              nn.ReLU(True)]
            UpBlock2 += [nn.ReflectionPad2d(1),   
                         nn.Conv2d(ngf * mult, int(ngf * mult / 2), kernel_size=3, stride=1, padding=0, bias=False),
                         ILN(int(ngf * mult / 2)),
                         nn.ReLU(True),
                         nn.Conv2d(int(ngf * mult / 2), int(ngf * mult / 2)*4, kernel_size=1, stride=1, bias=True),
                         nn.PixelShuffle(2),
                         ILN(int(ngf * mult / 2)),
                         nn.ReLU(True)
                         ]
            UpBlock3 += [nn.ReflectionPad2d(1),   
                         nn.Conv2d(ngf * mult, int(ngf * mult / 2), kernel_size=3, stride=1, padding=0, bias=False),
                         ILN(int(ngf * mult / 2)),
                         nn.ReLU(True),
                         nn.Conv2d(int(ngf * mult / 2), int(ngf * mult / 2)*4, kernel_size=1, stride=1, bias=True),
                         nn.PixelShuffle(2),
                         ILN(int(ngf * mult / 2)),
                         nn.ReLU(True)
                         ]

        UpBlock4 += [nn.ReflectionPad2d(3),
                     nn.Conv2d(ngf * 2, output_nc, kernel_size=7, stride=1, padding=0, bias=False),
                     nn.Tanh()]
        

        self.FC = nn.Sequential(*FC)
        self.UpBlock0 = nn.Sequential(*UpBlock0)
        self.UpBlock2 = nn.Sequential(*UpBlock2)
        self.UpBlock3 = nn.Sequential(*UpBlock3)
        self.UpBlock4 = nn.Sequential(*UpBlock4)
        self.conv_block = nn.Sequential(*conv_block)
        self.conv_block1 = nn.Sequential(*conv_block1)
        self.mult = 4

    def forward(self, z):
        x = z
        x = self.UpBlock0(x)

        if self.light:
            x_ = torch.nn.functional.adaptive_avg_pool2d(x, 1)
            x_ = self.FC(x_.view(x_.shape[0], -1))
        else:
            x_ = self.FC(x.view(x.shape[0], -1))
        gamma, beta = self.gamma(x_), self.beta(x_)

        outat = torch.reshape(x, (self.ngf * self.mult,  self.ngf,  self.ngf))
        outat, _ = self.atten(outat, outat, outat)
        xa11 = xa1 = outat = self.attconv(self.attnorm(self.attrelu(torch.reshape(outat, (1,  self.ngf * self.mult,  self.ngf,  self.ngf))), gamma, beta))
        if self.n_blocks>1:
          for i in range(2, self.n_blocks+1):
            if i%3 == 2:
              outat = torch.reshape(outat, ( self.ngf * self.mult,  self.ngf,  self.ngf))
              outat, _ = self.atten(outat, outat, outat)
              xa2 = outat = self.attconv(self.attnorm(self.attrelu(torch.reshape(outat, (1,  self.ngf * self.mult,  self.ngf,  self.ngf))), gamma, beta))
            elif i%3 == 0:
              outat = torch.reshape(outat + xa1, ( self.ngf * self.mult,  self.ngf,  self.ngf))
              outat, _ = self.atten(outat, outat, outat)
              xa3 = outat = self.attconv(self.attnorm(self.attrelu(torch.reshape(outat, (1, self.ngf * self.mult,  self.ngf,  self.ngf))), gamma, beta))
            elif i < 6:
              outat = torch.reshape(outat + xa1 + xa2, ( self.ngf * self.mult,  self.ngf,  self.ngf))
              outat, _ = self.atten(outat, outat, outat)
              xa1 = outat = self.attconv(self.attnorm(self.attrelu(torch.reshape(outat, (1,  self.ngf * self.mult,  self.ngf,  self.ngf))), gamma, beta))
            else:
              outat = torch.reshape(outat + xa1 + xa2 + xa11, ( self.ngf * self.mult,  self.ngf,  self.ngf))
              outat, _ = self.atten(outat, outat, outat)
              xa11 = xa1
              xa1 = outat = self.attconv(self.attnorm(self.attrelu(torch.reshape(outat, (1,  self.ngf * self.mult,  self.ngf,  self.ngf))), gamma, beta))

        outat = self.conv_block(outat)
        x = self.conv_block1(x + outat)     

        out = self.UpBlock4(torch.cat([self.UpBlock2(x), self.UpBlock3(x)],1))

        return out




class adaILN(nn.Module):
    def __init__(self, num_features, eps=1e-5, momentum=0.9, using_moving_average=True, using_bn=False):
        super(adaILN, self).__init__()
        self.eps = eps
        self.momentum = momentum
        self.using_moving_average = using_moving_average
        self.using_bn = using_bn
        self.num_features = num_features
    
        if self.using_bn:
            self.rho = Parameter(torch.Tensor(1, num_features, 3))
            self.rho[:,:,0].data.fill_(3)
            self.rho[:,:,1].data.fill_(1)
            self.rho[:,:,2].data.fill_(1)
            self.register_buffer('running_mean', torch.zeros(1, num_features, 1,1))
            self.register_buffer('running_var', torch.zeros(1, num_features, 1,1))
            self.running_mean.zero_()
            self.running_var.zero_()
        else:
            self.rho = Parameter(torch.Tensor(1, num_features, 2))
            self.rho[:,:,0].data.fill_(3.2)
            self.rho[:,:,1].data.fill_(1)

    def forward(self, input, gamma, beta):
        in_mean, in_var = torch.mean(input, dim=[2, 3], keepdim=True), torch.var(input, dim=[2, 3], keepdim=True)
        out_in = (input - in_mean) / torch.sqrt(in_var + self.eps)
        ln_mean, ln_var = torch.mean(input, dim=[1, 2, 3], keepdim=True), torch.var(input, dim=[1, 2, 3], keepdim=True)
        out_ln = (input - ln_mean) / torch.sqrt(ln_var + self.eps)
        softmax = nn.Softmax(2)
        rho = softmax(self.rho)
        
        
        if self.using_bn:
            if self.training:
                bn_mean, bn_var = torch.mean(input, dim=[0, 2, 3], keepdim=True), torch.var(input, dim=[0, 2, 3], keepdim=True)
                if self.using_moving_average:
                    self.running_mean.mul_(self.momentum)
                    self.running_mean.add_((1 - self.momentum) * bn_mean.data)
                    self.running_var.mul_(self.momentum)
                    self.running_var.add_((1 - self.momentum) * bn_var.data)
                else:
                    self.running_mean.add_(bn_mean.data)
                    self.running_var.add_(bn_mean.data ** 2 + bn_var.data)
            else:
                bn_mean = torch.autograd.Variable(self.running_mean)
                bn_var = torch.autograd.Variable(self.running_var)
            out_bn = (input - bn_mean) / torch.sqrt(bn_var + self.eps)
            rho_0 = rho[:,:,0]
            rho_1 = rho[:,:,1]
            rho_2 = rho[:,:,2]

            rho_0 = rho_0.view(1, self.num_features, 1,1)
            rho_1 = rho_1.view(1, self.num_features, 1,1)
            rho_2 = rho_2.view(1, self.num_features, 1,1)
            rho_0 = rho_0.expand(input.shape[0], -1, -1, -1)
            rho_1 = rho_1.expand(input.shape[0], -1, -1, -1)
            rho_2 = rho_2.expand(input.shape[0], -1, -1, -1)
            out = rho_0 * out_in + rho_1 * out_ln + rho_2 * out_bn
        else:
            rho_0 = rho[:,:,0]
            rho_1 = rho[:,:,1]
            rho_0 = rho_0.view(1, self.num_features, 1,1)
            rho_1 = rho_1.view(1, self.num_features, 1,1)
            rho_0 = rho_0.expand(input.shape[0], -1, -1, -1)
            rho_1 = rho_1.expand(input.shape[0], -1, -1, -1)
            out = rho_0 * out_in + rho_1 * out_ln

        out = out * gamma.unsqueeze(2).unsqueeze(3) + beta.unsqueeze(2).unsqueeze(3)
        return out


class ILN(nn.Module):
    def __init__(self, num_features, eps=1e-5, momentum=0.9, using_moving_average=True, using_bn=False):
        super(ILN, self).__init__()
        self.eps = eps
        self.momentum = momentum
        self.using_moving_average = using_moving_average
        self.using_bn = using_bn
        self.num_features = num_features
    
        if self.using_bn:
            self.rho = Parameter(torch.Tensor(1, num_features, 3))
            self.rho[:,:,0].data.fill_(1)
            self.rho[:,:,1].data.fill_(3)
            self.rho[:,:,2].data.fill_(3)
            self.register_buffer('running_mean', torch.zeros(1, num_features, 1,1))
            self.register_buffer('running_var', torch.zeros(1, num_features, 1,1))
            self.running_mean.zero_()
            self.running_var.zero_()
        else:
            self.rho = Parameter(torch.Tensor(1, num_features, 2))
            self.rho[:,:,0].data.fill_(1)
            self.rho[:,:,1].data.fill_(3.2)

        self.gamma = Parameter(torch.Tensor(1, num_features, 1, 1))
        self.beta = Parameter(torch.Tensor(1, num_features, 1, 1))
        self.gamma.data.fill_(1.0)
        self.beta.data.fill_(0.0)

    def forward(self, input):
        in_mean, in_var = torch.mean(input, dim=[2, 3], keepdim=True), torch.var(input, dim=[2, 3], keepdim=True)
        out_in = (input - in_mean) / torch.sqrt(in_var + self.eps)
        ln_mean, ln_var = torch.mean(input, dim=[1, 2, 3], keepdim=True), torch.var(input, dim=[1, 2, 3], keepdim=True)
        out_ln = (input - ln_mean) / torch.sqrt(ln_var + self.eps)
        
        softmax = nn.Softmax(2)
        rho = softmax(self.rho)
        
        if self.using_bn:
            if self.training:
                bn_mean, bn_var = torch.mean(input, dim=[0, 2, 3], keepdim=True), torch.var(input, dim=[0, 2, 3], keepdim=True)
                if self.using_moving_average:
                    self.running_mean.mul_(self.momentum)
                    self.running_mean.add_((1 - self.momentum) * bn_mean.data)
                    self.running_var.mul_(self.momentum)
                    self.running_var.add_((1 - self.momentum) * bn_var.data)
                else:
                    self.running_mean.add_(bn_mean.data)
                    self.running_var.add_(bn_mean.data ** 2 + bn_var.data)
            else:
                bn_mean = torch.autograd.Variable(self.running_mean)
                bn_var = torch.autograd.Variable(self.running_var)
            out_bn = (input - bn_mean) / torch.sqrt(bn_var + self.eps)
            rho_0 = rho[:,:,0]
            rho_1 = rho[:,:,1]
            rho_2 = rho[:,:,2]

            rho_0 = rho_0.view(1, self.num_features, 1,1)
            rho_1 = rho_1.view(1, self.num_features, 1,1)
            rho_2 = rho_2.view(1, self.num_features, 1,1)
            rho_0 = rho_0.expand(input.shape[0], -1, -1, -1)
            rho_1 = rho_1.expand(input.shape[0], -1, -1, -1)
            rho_2 = rho_2.expand(input.shape[0], -1, -1, -1)
            out = rho_0 * out_in + rho_1 * out_ln + rho_2 * out_bn
        else:
            rho_0 = rho[:,:,0]
            rho_1 = rho[:,:,1]
            rho_0 = rho_0.view(1, self.num_features, 1,1)
            rho_1 = rho_1.view(1, self.num_features, 1,1)
            rho_0 = rho_0.expand(input.shape[0], -1, -1, -1)
            rho_1 = rho_1.expand(input.shape[0], -1, -1, -1)
            out = rho_0 * out_in + rho_1 * out_ln
        
        out = out * self.gamma.expand(input.shape[0], -1, -1, -1) + self.beta.expand(input.shape[0], -1, -1, -1)
        return out


class Discriminator(nn.Module):
    def __init__(self, input_nc, ndf=64, n_layers=7):
        super(Discriminator, self).__init__() 

        # proposed Encoder
        enc1 = [nn.ReflectionPad2d(1),
                 nn.utils.spectral_norm(
                 nn.Conv2d(input_nc, ndf, kernel_size=3, stride=1, padding=0, bias=True)),
                 nn.ReLU(True)]
                 
        enc11 = [nn.Conv2d(ndf, ndf*2, kernel_size=1, stride=1, bias=True),
                 nn.MaxPool2d(4),
                 nn.ReLU(True)
                 ]
        enc2 = [nn.ReflectionPad2d(1),
                 nn.utils.spectral_norm(
                 nn.Conv2d(ndf, ndf*2, kernel_size=3, stride=2, padding=0, bias=True)),
                 nn.ReLU(True)
                 ]
        enc22 = [nn.MaxPool2d(2),
                 nn.ReLU(True)]
        enc3 = [nn.ReflectionPad2d(1),
                 nn.utils.spectral_norm(
                 nn.Conv2d(ndf*2, ndf*4, kernel_size=3, stride=2, padding=0, bias=True)),
                 nn.ReLU(True)
                 ]
        enc33 = [nn.Conv2d(ndf*4, ndf*2, kernel_size=1, stride=1, bias=True),
                 nn.ReLU(True)]

        #Proposed adaptive feature fution.
        self.softmaxAFF = nn.Softmax(3)
        AFF1 = [nn.ReflectionPad2d(1),
                nn.Conv2d(ndf*2, 1, kernel_size=3, stride=1, padding=0, bias=True),
                nn.InstanceNorm2d(ndf*2)]
        AFF2 = [nn.ReflectionPad2d(1),
                nn.Conv2d(ndf*2, 1, kernel_size=3, stride=1, padding=0, bias=True)]
        AFF = [nn.Conv2d(3*ndf*2, ndf*2, kernel_size=1, stride=1, padding=0, bias=True),
               nn.ReflectionPad2d(1),
               nn.Conv2d(ndf*2, ndf*2, kernel_size=3, stride=1, padding=0, bias=True)]
        
        
        # Class Activation Map
        mult = 2 ** (1)
        self.fc = nn.utils.spectral_norm(nn.Linear(ndf * mult * 2, 1, bias=False))
        self.conv1x1 = nn.Conv2d(ndf * mult * 2, ndf * mult, kernel_size=1, stride=1, bias=True)
        self.leaky_relu = nn.LeakyReLU(0.2, True)
        self.lamda = nn.Parameter(torch.zeros(1))


        #Discriminator
        Dis1 = [nn.ReflectionPad2d(1),
                      nn.utils.spectral_norm(
                      nn.Conv2d(ndf, ndf, kernel_size=4, stride=2, padding=0, bias=True)),
                      nn.LeakyReLU(0.2, True),
                      nn.ReflectionPad2d(1),
                      nn.utils.spectral_norm(
                      nn.Conv2d(ndf, ndf*2, kernel_size=4, stride=2, padding=0, bias=True)),
                      nn.LeakyReLU(0.2, True),
                      nn.ReflectionPad2d(1),
                      nn.utils.spectral_norm(
                      nn.Conv2d(ndf*2, ndf*4, kernel_size=4, stride=2, padding=0, bias=True)),
                      nn.LeakyReLU(0.2, True)]
        Dis2 = [nn.ReflectionPad2d(1),
                      nn.utils.spectral_norm(
                      nn.Conv2d(ndf*2, ndf*2, kernel_size=4, stride=2, padding=0, bias=True)),
                      nn.LeakyReLU(0.2, True),
                      nn.ReflectionPad2d(1),
                      nn.utils.spectral_norm(
                      nn.Conv2d(ndf*2, ndf*4, kernel_size=4, stride=2, padding=0, bias=True)),
                      nn.LeakyReLU(0.2, True),
                      nn.ReflectionPad2d(1),
                      nn.utils.spectral_norm(
                      nn.Conv2d(ndf*4, ndf*8, kernel_size=4, stride=2, padding=0, bias=True)),
                      nn.LeakyReLU(0.2, True)]
        Dis3 = [nn.ReflectionPad2d(1),
                      nn.utils.spectral_norm(
                      nn.Conv2d(ndf*4, ndf*4, kernel_size=4, stride=2, padding=0, bias=True)),
                      nn.LeakyReLU(0.2, True),
                      nn.ReflectionPad2d(1),
                      nn.utils.spectral_norm(
                      nn.Conv2d(ndf*4, ndf*8, kernel_size=4, stride=2, padding=0, bias=True)),
                      nn.LeakyReLU(0.2, True),
                      nn.ReflectionPad2d(1),
                      nn.utils.spectral_norm(
                      nn.Conv2d(ndf*8, ndf*16, kernel_size=4, stride=2, padding=0, bias=True)),
                      nn.LeakyReLU(0.2, True)]
        
        self.conv1 = nn.utils.spectral_norm(   #1+3*2^0 + 3*2^1 + 3*2^2 +3*2^3 + 3*2^3= 70
            nn.Conv2d(ndf*4, 1, kernel_size=4, stride=1, padding=0, bias=False))
        self.conv2 = nn.utils.spectral_norm(
            nn.Conv2d(ndf*8, 1, kernel_size=4, stride=1, padding=0, bias=False))
        self.conv3 = nn.utils.spectral_norm(
            nn.Conv2d(ndf*16, 1, kernel_size=4, stride=1, padding=0, bias=False))
        

        self.pad = nn.ReflectionPad2d(1)

        self.Dis1 = nn.Sequential(*Dis1)
        self.Dis2 = nn.Sequential(*Dis2)
        self.Dis3 = nn.Sequential(*Dis3)
        
        self.enc1 = nn.Sequential(*enc1)
        self.enc2 = nn.Sequential(*enc2)
        self.enc3 = nn.Sequential(*enc3)
        self.enc11 = nn.Sequential(*enc11)
        self.enc22 = nn.Sequential(*enc22)
        self.enc33 = nn.Sequential(*enc33)
        self.AFF1 = nn.Sequential(*AFF1)
        self.AFF2 = nn.Sequential(*AFF2)
        self.AFF = nn.Sequential(*AFF)

    def forward(self, input):
      
        x1 = self.enc1(input)
        x2 = self.enc2(x1)
        x3 = self.enc3(x2)

        x11 = self.enc11(x1)
        x22 = self.enc22(x2)
        x33 = self.enc33(x3)

        #x11 = x11 * self.softmaxAFF(self.AFF1(x11))
        #x33 = x33 * self.softmaxAFF(self.AFF3(x33))
        x33 = x33 * self.softmaxAFF(self.AFF1(x33))
        x22 = x22 * self.softmaxAFF(self.AFF2(x22))

        x_0 = x = self.AFF(torch.cat([x11, x22, x33], 1))

        #x_0 = x = self.model(input)

        gap = torch.nn.functional.adaptive_avg_pool2d(x, 1)
        gmp = torch.nn.functional.adaptive_max_pool2d(x, 1)
        x = torch.cat([x, x], 1)
        cam_logit = torch.cat([gap, gmp], 1)
        cam_logit = self.fc(cam_logit.view(cam_logit.shape[0], -1))
        weight = list(self.fc.parameters())[0]
        x = x * weight.unsqueeze(2).unsqueeze(3)
        x = self.conv1x1(x)

        x = self.lamda*x + x_0
        x = self.leaky_relu(x)
        
        heatmap = torch.sum(x, dim=1, keepdim=True)
        z = x

        x1 = self.Dis1(x1)
        x2 = self.Dis2(x2)
        x3 = self.Dis3(x3)
        x1 = self.pad(x1)
        x2 = self.pad(x2)
        x3 = self.pad(x3)
        
        out1 = self.conv1(x1)
        out2 = self.conv2(x2)
        out3 = self.conv3(x3)
        
        return out1, out2, out3, cam_logit, heatmap, z

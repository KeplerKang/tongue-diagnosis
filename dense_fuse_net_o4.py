import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import math


# Convolution operation
class ConvLayer(torch.nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size, stride, is_last=False):
        super(ConvLayer, self).__init__()
        reflection_padding = int(np.floor(kernel_size / 2))
        self.reflection_pad = nn.ReflectionPad2d(reflection_padding)
        self.conv2d = nn.Conv2d(in_channels, out_channels, kernel_size, stride)
        self.is_last = is_last

    def forward(self, x):
        out = self.reflection_pad(x)
        out = self.conv2d(out)
        if self.is_last is False:
            out = F.relu(out, inplace=True)
        return out


# Dense convolution unit
class DenseConv(torch.nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size, stride):
        super(DenseConv, self).__init__()
        self.dense_conv = ConvLayer(in_channels, out_channels, kernel_size, stride)


    def forward(self, x):
        out = self.dense_conv(x)
        out = torch.cat([x, out], 1)
        return out


# Dense Block unit
class DenseBlock(torch.nn.Module):
    def __init__(self, in_channels, out_channels_def, kernel_size, stride):
        super(DenseBlock, self).__init__()

        denseblock = []
        denseblock += [DenseConv(in_channels, out_channels_def, kernel_size, stride),
                       DenseConv(in_channels+out_channels_def, out_channels_def, kernel_size, stride),
                       DenseConv(in_channels+out_channels_def*2, out_channels_def, kernel_size, stride)]
        self.denseblock = nn.Sequential(*denseblock)

    def forward(self, x):
        out = self.denseblock(x)
        return out

    
class Down(nn.Module):
    def __init__(self, in_channels, out_channels):
        super(Down, self).__init__()
        denseblock = DenseBlock
        self.en = nn.Sequential(
            nn.MaxPool2d(2),  # C:64 HW:128->64
            nn.Conv2d(in_channels, in_channels//2, kernel_size=1, stride=1),  # C:64->32
            denseblock(in_channels//2, in_channels//2, kernel_size=3, stride=1),  # C:32->128 64*64
            nn.Conv2d(in_channels*2, out_channels, kernel_size=1, stride=1),
        )

    def forward(self, x):
            return self.en(x)
        
class ResizeLike(nn.Module):
    def __init__(self):
        super(ResizeLike, self).__init__()
    
    def forward(self, src, tar, mode='bilinear', align_corners=True):
        src = F.interpolate(src, size=tar.shape[2:], mode=mode, align_corners=align_corners)
        return src
        

class Up(nn.Module):
    def __init__(self, in_channels, out_channels):
        super(Up, self).__init__()
        denseblock = DenseBlock
        kernel_size = 3
        stride = 1
        self.resize = ResizeLike()
        self.de = nn.Sequential(
            # nn.Upsample(scale_factor=2, mode='bilinear', align_corners=True),
            ConvLayer(in_channels, in_channels, kernel_size, stride),
            ConvLayer(in_channels, out_channels, kernel_size, stride)
        )
        self.conv = ConvLayer(in_channels, out_channels,kernel_size, stride)
        
    def forward(self, x1, x2):
        x1 = self.resize(x1, x2)
        x1 = self.de(x1)
        x = torch.cat([x2, x1], dim=1)
        return self.conv(x)

    
# DenseFuse network
class DenseFuseNet(nn.Module):
    def __init__(self, input_nc=3, output_nc=2):
        super(DenseFuseNet, self).__init__()
        kernel_size = 3
        stride = 1

        self.inc = ConvLayer(input_nc, 32, kernel_size, stride)
        self.en1 = Down(32, 64)
        self.en2 = Down(64, 128)
        self.en3 = Down(128, 256)
        self.en4 = Down(256, 512)

        self.maxPool2 = nn.MaxPool2d(kernel_size=2, stride=2)
        self.maxPool4 = nn.MaxPool2d(kernel_size=4, stride=4)
        self.maxPool8 = nn.MaxPool2d(kernel_size=8, stride=8)
        self.maxPool16 = nn.MaxPool2d(kernel_size=16, stride=16)
        
        self.conv2 = nn.Conv2d(96, 64, 1, 1)
        self.conv3 = nn.Conv2d(192, 128, 1, 1)
        self.conv4 = nn.Conv2d(384, 256, 1, 1)
        self.conv5 = nn.Conv2d(768, 512, 1, 1)

        self.de1 = Up(512, 256)
        self.de2 = Up(256, 128)
        self.de3 = Up(128, 64)
        self.de4 = Up(64, 32)
        self.outc1 = ConvLayer(256, output_nc, kernel_size, stride, is_last=True)
        self.outc2 = ConvLayer(128, output_nc, kernel_size, stride, is_last=True)
        self.outc3 = ConvLayer(64, output_nc, kernel_size, stride, is_last=True)
        self.outc4 = ConvLayer(32, output_nc, kernel_size, stride, is_last=True)
        

    def en(self, x):
        x1 = self.inc(x)
        x2 = self.en1(x1)
        x3 = self.en2(x2)
        x4 = self.en3(x3)
        x5 = self.en4(x4)
        x_list = [x5, x4, x3, x2, x1]
        return x_list

    def de(self, x5, x4, x3, x2, x1):
        x = self.de1(x5, x4)
        x = self.de2(x, x3)
        x = self.de3(x, x2)
        x = self.de4(x, x1)
        out = self.outc(x)
        return out


    def forward(self, x):
        [x5, x4, x3, x2, x1] = self.en(x)
        # BFA
        x1_F = self.maxPool2(x1)
        x2 = torch.cat((x1_F, x2), 1)
        x2 = self.conv2(x2)
        
        x2_F = self.maxPool2(x2)
        x3 = torch.cat((x2_F, x3), 1)
        x3 = self.conv3(x3)
        
        x3_F = self.maxPool2(x3)
        x4 = torch.cat((x3_F, x4), 1)
        x4 = self.conv4(x4)
        
        x4_F = self.maxPool2(x4)
        x5 = torch.cat((x4_F, x5), 1)
        x5 = self.conv5(x5)

        out1 = self.de1(x5, x4)
        out2 = self.de2(out1, x3)
        out3 = self.de3(out2, x2)
        out4 = self.de4(out3, x1)
        
        out = self.outc4(out4)
        out3 = self.outc3(out3)
        out2 = self.outc2(out2)
        out1 = self.outc1(out1)
        
        return out, out3, out2, out1

    
class DenseFuseNetPlus(nn.Module):
    def __init__(self, input_nc=3, output_nc=3):
        super(DenseFuseNetPlus, self).__init__()
        kernel_size = 3
        stride = 1

        self.inc = ConvLayer(input_nc, 64, kernel_size, stride)
        self.en1 = Down(64, 128)
        self.en2 = Down(128, 256)
        self.en3 = Down(256, 512)
        self.en4 = Down(512, 512)

        self.maxPool2 = nn.MaxPool2d(kernel_size=2, stride=2)
        self.maxPool4 = nn.MaxPool2d(kernel_size=4, stride=4)
        self.maxPool8 = nn.MaxPool2d(kernel_size=8, stride=8)
        self.maxPool16 = nn.MaxPool2d(kernel_size=16, stride=16)
        
        self.conv1 = nn.Conv2d(64, 32, 1, 1)
        self.conv2 = nn.Conv2d(160, 64, 1, 1)
        self.conv3 = nn.Conv2d(320, 128, 1, 1)
        self.conv4 = nn.Conv2d(640, 256, 1, 1)
        self.conv5 = nn.Conv2d(768,512, 1, 1)

        self.de1 = Up(512, 256)
        self.de2 = Up(256, 128)
        self.de3 = Up(128, 64)
        self.de4 = Up(64, 32)
        self.outc1 = ConvLayer(256, output_nc, kernel_size, stride, is_last=True)
        self.outc2 = ConvLayer(128, output_nc, kernel_size, stride, is_last=True)
        self.outc3 = ConvLayer(64, output_nc, kernel_size, stride, is_last=True)
        self.outc4 = ConvLayer(32, output_nc, kernel_size, stride, is_last=True)
        

    def en(self, x):
        x1 = self.inc(x)
        x2 = self.en1(x1)
        x3 = self.en2(x2)
        x4 = self.en3(x3)
        x5 = self.en4(x4)
        x_list = [x5, x4, x3, x2, x1]
        return x_list

    def de(self, x5, x4, x3, x2, x1):
        x = self.de1(x5, x4)
        x = self.de2(x, x3)
        x = self.de3(x, x2)
        x = self.de4(x, x1)
        out = self.outc(x)
        return out


    def forward(self, x):
        [x5, x4, x3, x2, x1] = self.en(x)
        x1 = self.conv1(x1)
        # BFA
        x1_F = self.maxPool2(x1)
        x2 = torch.cat((x1_F, x2), 1)
        x2 = self.conv2(x2)
        
        x2_F = self.maxPool2(x2)
        x3 = torch.cat((x2_F, x3), 1)
        x3 = self.conv3(x3)
        
        x3_F = self.maxPool2(x3)
        x4 = torch.cat((x3_F, x4), 1)
        x4 = self.conv4(x4)
        
        x4_F = self.maxPool2(x4)
        x5 = torch.cat((x4_F, x5), 1)
        x5 = self.conv5(x5)

        out1 = self.de1(x5, x4)
        out2 = self.de2(out1, x3)
        out3 = self.de3(out2, x2)
        out4 = self.de4(out3, x1)
        
        out = self.outc4(out4)
        out3 = self.outc3(out3)
        out2 = self.outc2(out2)
        out1 = self.outc1(out1)
        
        return out, out3, out2, out1
    
    
    
class DenseFuseNetPlusPlus(nn.Module):
    def __init__(self, input_nc=3, output_nc=3):
        super(DenseFuseNetPlusPlus, self).__init__()
        kernel_size = 3
        stride = 1

        self.inc = ConvLayer(input_nc, 64, kernel_size, stride)
        self.en1 = Down(64, 128)
        self.en2 = Down(128, 256)
        self.en3 = Down(256, 512)
        self.en4 = Down(512, 1024)

        self.maxPool2 = nn.MaxPool2d(kernel_size=2, stride=2)
        self.maxPool4 = nn.MaxPool2d(kernel_size=4, stride=4)
        self.maxPool8 = nn.MaxPool2d(kernel_size=8, stride=8)
        self.maxPool16 = nn.MaxPool2d(kernel_size=16, stride=16)
        
        self.conv1 = nn.Conv2d(64, 32, 1, 1)
        self.conv2 = nn.Conv2d(160, 64, 1, 1)
        self.conv3 = nn.Conv2d(320, 128, 1, 1)
        self.conv4 = nn.Conv2d(640, 256, 1, 1)
        self.conv5 = nn.Conv2d(1280,512, 1, 1)

        self.de1 = Up(512, 256)
        self.de2 = Up(256, 128)
        self.de3 = Up(128, 64)
        self.de4 = Up(64, 32)
        self.outc1 = ConvLayer(256, output_nc, kernel_size, stride, is_last=True)
        self.outc2 = ConvLayer(128, output_nc, kernel_size, stride, is_last=True)
        self.outc3 = ConvLayer(64, output_nc, kernel_size, stride, is_last=True)
        self.outc4 = ConvLayer(32, output_nc, kernel_size, stride, is_last=True)
        

    def en(self, x):
        x1 = self.inc(x)
        x2 = self.en1(x1)
        x3 = self.en2(x2)
        x4 = self.en3(x3)
        x5 = self.en4(x4)
        x_list = [x5, x4, x3, x2, x1]
        return x_list

    def de(self, x5, x4, x3, x2, x1):
        x = self.de1(x5, x4)
        x = self.de2(x, x3)
        x = self.de3(x, x2)
        x = self.de4(x, x1)
        out = self.outc(x)
        return out


    def forward(self, x):
        [x5, x4, x3, x2, x1] = self.en(x)
        x1 = self.conv1(x1)
        # BFA
        x1_F = self.maxPool2(x1)
        x2 = torch.cat((x1_F, x2), 1)
        x2 = self.conv2(x2)
        
        x2_F = self.maxPool2(x2)
        x3 = torch.cat((x2_F, x3), 1)
        x3 = self.conv3(x3)
        
        x3_F = self.maxPool2(x3)
        x4 = torch.cat((x3_F, x4), 1)
        x4 = self.conv4(x4)
        
        x4_F = self.maxPool2(x4)
        x5 = torch.cat((x4_F, x5), 1)
        x5 = self.conv5(x5)

        out1 = self.de1(x5, x4)
        out2 = self.de2(out1, x3)
        out3 = self.de3(out2, x2)
        out4 = self.de4(out3, x1)
        
        out = self.outc4(out4)
        out3 = self.outc3(out3)
        out2 = self.outc2(out2)
        out1 = self.outc1(out1)
        
        return out, out3, out2, out1
import os
import argparse

from tqdm import tqdm
import pandas as pd
import joblib
import glob

from collections import OrderedDict
import torch
import torch.backends.cudnn as cudnn

import torch.optim as optim

import torchvision.transforms as transforms
from PIL import Image
from torch.utils.data import DataLoader, Dataset
from Networks.net import MODEL as net

from losses import ssim_ir, ssim_vi,RMI_ir,RMI_vi


device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument('--name', default='model name', help='model name: (default: arch+timestamp)')
    parser.add_argument('--epochs', default=10, type=int)
    parser.add_argument('--batch_size', default=64, type=int)
    parser.add_argument('--lr', '--learning-rate', default=1e-3, type=float)
    parser.add_argument('--weight', default=[1, 1,1,2.5], type=float)
    parser.add_argument('--betas', default=(0.9, 0.999), type=tuple)
    parser.add_argument('--eps', default=1e-8, type=float)
    parser.add_argument('--alpha', default=300, type=int,
                        help='number of new channel increases per depth (default: 300)')
    args = parser.parse_args()

    return args





class GetDataset(Dataset):
    def __init__(self, MRIFolder, PETFolder, transform=None):
        self.MRIFolder = MRIFolder
        self.PETFolder = PETFolder
        self.transform = transform
        self.mri_files = sorted(os.listdir(MRIFolder))
        self.pet_files = sorted(os.listdir(PETFolder))
        
    def __getitem__(self, index):
        mri_path = os.path.join(MRIFolder, self.mri_files[index])
        pet_path = os.path.join(PETFolder, self.pet_files[index])
        mri_image = Image.open(mri_path).convert('L')
        pet_image0 = cv2.imread(pet_path)
        pet_image0 = pet_image0[:,:,0]
        background_threshold = 1
        background_mask = x < background_threshold
        transparent_color = (0,0,0,1)  # black color (1, 1, 1) with alpha 0 (fully transparent)
        color_map = plt.get_cmap('jet')
        pet_image = color_map(pet_image0)
        pet_image[background_mask] = transparent_color
        
        if self.transform:
            mri_image = self.transform(mri_image)
            pet_image = self.transform(pet_image)
            
        input = torch.cat((pet_image, mri_image), -3)
        return input, pet_image, mri_image
        
    def __len__(self):
        return len(self.mri_files)


class AverageMeter(object):

    def __init__(self):
        self.reset()

    def reset(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count


def train(args, train_loader_ir, model, criterion_ssim_ir, criterion_ssim_vi,criterion_RMI_ir,criterion_RMI_vi,optimizer, epoch, scheduler=None):
    losses = AverageMeter()
    losses_ssim_ir = AverageMeter()
    losses_ssim_vi = AverageMeter()
    losses_RMI_ir = AverageMeter()
    losses_RMI_vi= AverageMeter()
    weight = args.weight
    model.train()

    for i, (input,ir,vi)  in tqdm(enumerate(train_loader_ir), total=len(train_loader_ir)):

        if use_gpu:
            ir=ir.to(device)
            vi=vi.to(device)
        else:
            input = input
            ir=ir
            vi=vi

        out = model(input)

        loss_ssim_ir= weight[0] * criterion_ssim_ir(out, ir)
        loss_ssim_vi= weight[1] * criterion_ssim_vi(out, vi)
        loss_RMI_ir= weight[2] * criterion_RMI_ir(out,ir)
        loss_RMI_vi = weight[3] * criterion_RMI_vi(out,vi)
        loss = loss_ssim_ir + loss_ssim_vi+loss_RMI_ir+ loss_RMI_vi

        losses.update(loss.item(), input.size(0))
        losses_ssim_ir.update(loss_ssim_ir.item(), input.size(0))
        losses_ssim_vi.update(loss_ssim_vi.item(), input.size(0))
        losses_RMI_ir.update(loss_RMI_ir.item(), input.size(0))
        losses_RMI_vi.update(loss_RMI_vi.item(), input.size(0))

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

    log = OrderedDict([
        ('loss', losses.avg),
        ('loss_ssim_ir', losses_ssim_ir.avg),
        ('loss_ssim_vi', losses_ssim_vi.avg),
        ('loss_RMI_ir', losses_RMI_ir.avg),
        ('loss_RMI_vi', losses_RMI_vi.avg),
    ])

    return log



def main():
    args = parse_args()

    if not os.path.exists('models/%s' %args.name):
        os.makedirs('models/%s' %args.name)

    print('Config -----')
    for arg in vars(args):
        print('%s: %s' %(arg, getattr(args, arg)))
    print('------------')

    with open('models/%s/args.txt' %args.name, 'w') as f:
        for arg in vars(args):
            print('%s: %s' %(arg, getattr(args, arg)), file=f)

    joblib.dump(args, 'models/%s/args.pkl' %args.name)
    cudnn.benchmark = True

    folder_dataset_train_ir = '/kaggle/working/mat/MATR-main/PET'
    folder_dataset_train_vi= '/kaggle/working/mat/MATR-main/MRI'
    transform_train = transforms.Compose([transforms.ToTensor(),
                                          transforms.Normalize((0.485, 0.456, 0.406),
                                                               (0.229, 0.224, 0.225))
                                          ])

    dataset_train = GetDataset(MRIFolder=folder_dataset_train_vi, PETFolder=folder_dataset_train_ir,transform=transform_train)
    train_loader_ir = DataLoader(dataset_train,shuffle=True,batch_size=args.batch_size)
    model = net(in_channel=2)
    import torch.nn as nn
    model = nn.DataParallel(model, device_ids=[0, 1])
    model = model.to(device)
    criterion_ssim_ir = ssim_ir
    criterion_ssim_vi = ssim_vi
    criterion_RMI_ir = RMI_ir
    criterion_RMI_vi=RMI_vi
    optimizer = optim.Adam(model.parameters(), lr=args.lr,
                           betas=args.betas, eps=args.eps)

    for epoch in range(args.epochs):
        print('Epoch [%d/%d]' % (epoch+1, args.epochs))

        train_log = train(args, train_loader_ir, model, criterion_ssim_ir, criterion_ssim_vi,criterion_RMI_ir,criterion_RMI_vi, optimizer, epoch)     # 训练集

        print('loss: %.4f - loss_ssim_ir: %.4f - loss_ssim_vi: %.4f - loss_RMI_ir: %.4f - loss_RMI_vi: %.4f '
              % (train_log['loss'],
                 train_log['loss_ssim_ir'],
                 train_log['loss_ssim_vi'],
                 train_log['loss_RMI_ir'],
                 train_log['loss_RMI_vi'],
                 ))
        
        if (epoch+1) % 1 == 0:
            torch.save(model.state_dict(), 'models/%s/model_{}.pth'.format(epoch+1) %args.name)
        

if __name__ == '__main__':
    main()


